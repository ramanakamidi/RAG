import os
import re
import time
import uuid
from io import BytesIO
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

# ----------------------------- CONFIG -----------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.6-flash")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "api")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "gemini-embedding-2")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set.")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is not set.")

CHUNK_SIZE = 100
CHUNK_OVERLAP = 20
TOP_K = 3
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))

# ----------------------------- CLIENTS -----------------------------

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

_embedding_model = None
_gemini_client = None


def effective_embedding_model() -> str:
    name = EMBEDDING_MODEL_NAME.strip()
    if EMBEDDING_PROVIDER.strip().lower() == "api" and not re.match(
        r"^(models/)?(gemini|text)-[a-z0-9.-]+", name
    ):
        print(
            f"[warn] EMBEDDING_MODEL_NAME={name!r} is not a Gemini API model; "
            "falling back to gemini-embedding-2 for EMBEDDING_PROVIDER=api.",
            flush=True,
        )
        return "gemini-embedding-2"
    return name

# ----------------------------- HELPERS -----------------------------


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def embed_texts(texts: List[str]) -> List[List[float]]:
    provider = EMBEDDING_PROVIDER.strip().lower()
    if provider == "api":
        return _embed_texts_gemini_api(texts)
    if provider == "local":
        return get_embedding_model().encode(texts).tolist()
    raise RuntimeError(f"Unknown EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER!r}")


def _embed_texts_batch(client, texts: List[str]) -> List[List[float]]:
    from google.genai import types

    response = client.models.embed_content(
        model=effective_embedding_model(),
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
    )
    return [e.values for e in response.embeddings]


def _embed_texts_gemini_api(texts: List[str]) -> List[List[float]]:
    client = get_gemini_client()
    results: List[List[float]] = []
    batch: List[str] = []
    batch_chars = 0
    for text in texts:
        if batch and batch_chars + len(text) > 20000:
            results.extend(_embed_texts_batch(client, batch))
            batch, batch_chars = [], 0
        batch.append(text)
        batch_chars += len(text)
    if batch:
        results.extend(_embed_texts_batch(client, batch))
    return results


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def _current_embedding_dimension() -> Optional[int]:
    try:
        with engine.connect() as conn:
            type_str = conn.execute(
                text(
                    "SELECT pg_catalog.format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_attribute a "
                    "WHERE a.attrelid = 'documents'::regclass AND a.attname = 'embedding'"
                )
            ).scalar()
    except Exception:
        return None
    if not type_str:
        return None
    match = re.search(r"\((\d+)\)", type_str)
    return int(match.group(1)) if match else None


def init_database():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector({EMBEDDING_DIMENSIONS})
                );
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS documents_embedding_idx "
                "ON documents USING hnsw (embedding vector_cosine_ops);"
            )
        )

    current = _current_embedding_dimension()
    if current is not None and current != EMBEDDING_DIMENSIONS:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS documents;"))
            conn.execute(
                text(
                    f"""
                    CREATE TABLE documents (
                        id TEXT PRIMARY KEY,
                        filename TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        embedding vector({EMBEDDING_DIMENSIONS})
                    );
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX documents_embedding_idx "
                    "ON documents USING hnsw (embedding vector_cosine_ops);"
                )
            )


def store_documents(filename: str, chunks: List[str], embeddings: List[List[float]]):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM documents WHERE filename = :filename"), {"filename": filename})
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            embedding_str = "[" + ",".join(str(float(v)) for v in embedding) + "]"
            conn.execute(
                text(
                    "INSERT INTO documents (id, filename, chunk_index, content, embedding) "
                    "VALUES (:id, :filename, :chunk_index, :content, CAST(:embedding AS vector))"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "filename": filename,
                    "chunk_index": i,
                    "content": chunk,
                    "embedding": embedding_str,
                },
            )


def retrieve_documents(question_embedding: List[float], top_k: int = TOP_K) -> List[str]:
    embedding_str = "[" + ",".join(str(float(v)) for v in question_embedding) + "]"
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT content FROM documents "
                "ORDER BY embedding <=> CAST(:embedding AS vector) "
                "LIMIT :limit"
            ),
            {"embedding": embedding_str, "limit": top_k},
        )
        return [row[0] for row in result.fetchall()]


# ----------------------------- APP -----------------------------

app = FastAPI(title="RAG Chatbot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str
    history: Optional[List[dict]] = None


@app.on_event("startup")
def on_startup():
    init_database()


@app.get("/")
def root():
    return {
        "message": "RAG Chatbot API is running",
        "endpoints": ["/health", "/upload", "/chat"],
    }


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    try:
        import google.genai as genai

        sdk_version = getattr(genai, "__version__", "unknown")
    except Exception:
        sdk_version = "unknown"
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "embedding_provider": EMBEDDING_PROVIDER,
        "embedding_model": effective_embedding_model(),
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "gemini_model": GEMINI_MODEL_NAME,
        "google_genai_version": sdk_version,
    }


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        content = await file.read()
        reader = PdfReader(BytesIO(content))

        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

        if not text.strip():
            raise HTTPException(status_code=400, detail="No text could be extracted from this PDF.")

        chunks = split_text(text)
        embeddings = embed_texts(chunks)

        store_documents(file.filename, chunks, embeddings)

        return {
            "filename": file.filename,
            "chunks": len(chunks),
            "words": len(text.split()),
            "message": f"Successfully indexed {len(chunks)} chunks.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


@app.post("/chat")
def chat(request: ChatRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        question_embedding = embed_texts([question])[0]

        retrieved_documents = retrieve_documents(question_embedding)

        if not retrieved_documents:
            return {"answer": "I don't know based on the provided text.", "sources": []}

        context = "\n\n".join(retrieved_documents)

        prompt = f"""
You are a helpful RAG question-answering chatbot.

Your job is to answer the user's question using ONLY
the information provided in the CONTEXT.

Do not use outside knowledge.

If the answer cannot be found in the CONTEXT, respond with:
"I don't know based on the provided text."

Keep the answer clear and concise.

CONTEXT:
--------------------------------------------------
{context}
--------------------------------------------------

USER QUESTION:
{question}

ANSWER:
"""

        gemini_client = get_gemini_client()

        last_error = None
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL_NAME,
                    contents=prompt,
                )
                return {"answer": response.text, "sources": retrieved_documents}
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        raise last_error
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get answer: {str(e)}")