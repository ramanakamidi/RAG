import React, { useRef, useState } from "react";
import "./App.css";

const BACKEND_URL =
  import.meta.env.VITE_BACKEND_URL || "";

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState(null);
  const [pdfName, setPdfName] = useState("");
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  React.useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  async function handleUpload(file) {
    if (!file || file.type !== "application/pdf") {
      setUploadStatus({ type: "error", text: "Please select a PDF file." });
      return;
    }

    setUploading(true);
    setUploadStatus(null);
    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${BACKEND_URL}/upload`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Upload failed.");
      }
      setUploadStatus({
        type: "success",
        text: `Indexed ${data.chunks} chunks from "${data.filename}". You can now ask questions about it.`,
      });
    } catch (e) {
      setUploadStatus({ type: "error", text: e.message });
    } finally {
      setUploading(false);
    }
  }

  async function sendMessage(e) {
    e.preventDefault();
    const question = input.trim();
    if (!question || loading) return;

    const userMessage = { role: "user", content: question };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to get answer.");
      }
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.answer, sources: data.sources },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleFileChange(e) {
    const file = e.target.files?.[0];
    if (file) {
      setPdfName(file.name);
      handleUpload(file);
    }
    e.target.value = "";
  }

  function clearChat() {
    setMessages([]);
    setUploadStatus(null);
  }

  return (
    <div className="app">
      <header className="header">
        <h1>RAG Chatbot</h1>
        <p>Upload a PDF, then ask questions based on its content.</p>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <h2>Knowledge Base</h2>

          <div className="upload-box">
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              onChange={handleFileChange}
              style={{ display: "none" }}
            />
            <button
              className="btn btn-upload"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? "Uploading & indexing..." : pdfName ? `Re-upload: ${pdfName}` : "Upload PDF"}
            </button>

            {uploadStatus && (
              <div className={`status ${uploadStatus.type}`}>{uploadStatus.text}</div>
            )}
          </div>

          <button className="btn btn-clear" onClick={clearChat}>
            Clear conversation
          </button>
        </aside>

        <main className="chat">
          <div className="messages">
            {messages.length === 0 && !loading && (
              <div className="empty-state">
                <p>No messages yet.</p>
                <p>Upload a PDF and start asking questions.</p>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`message ${msg.role}`}>
                <div className="bubble">{msg.content}</div>
                {msg.sources && msg.sources.length > 0 && (
                  <details className="sources">
                    <summary>Sources</summary>
                    {msg.sources.map((src, j) => (
                      <div key={j} className="source">
                        {src}
                      </div>
                    ))}
                  </details>
                )}
              </div>
            ))}

            {loading && (
              <div className="message assistant">
                <div className="bubble typing">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <form className="input-bar" onSubmit={sendMessage}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question about your document..."
              disabled={loading}
            />
            <button type="submit" className="btn btn-send" disabled={loading || !input.trim()}>
              Send
            </button>
          </form>
        </main>
      </div>
    </div>
  );
}

export default App;