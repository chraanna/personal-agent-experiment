document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("input");
  const conversation = document.getElementById("conversation");

  let hasStarted = false;

  const agentReplies = [
    "Jag hör dig.",
    "Mm. Berätta mer om du vill.",
    "Jag är med.",
    "Tack för att du delar det.",
    "Jag finns här."
  ];

  function addMessage(text, who) {
    const div = document.createElement("div");
    div.className = "message " + who;
    div.textContent = text;
    conversation.appendChild(div);
  }

  function isGreeting(text) {
    const normalized = text.toLowerCase().trim();
    return ["hej", "hej!", "hejsan", "hallå", "hello"].includes(normalized);
  }

  function randomAgentReply() {
    const index = Math.floor(Math.random() * agentReplies.length);
    return agentReplies[index];
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();

      const text = input.value.trim();
      if (!text) return;

      input.value = "";
      addMessage(text, "user");

      setTimeout(() => {
        // Första hälsningen – unikt svar
        if (!hasStarted && isGreeting(text)) {
          addMessage("Hej. Jag är här.", "agent");
          hasStarted = true;
          return;
        }

        // Första riktiga meddelandet
        if (!hasStarted) {
          addMessage("Jag lyssnar.", "agent");
          hasStarted = true;
          return;
        }

        // Vanliga svar
        addMessage(randomAgentReply(), "agent");
      }, 500);
    }
  });
});
