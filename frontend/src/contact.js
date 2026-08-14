const FORMSPREE_ID = import.meta.env.VITE_FORMSPREE_ID;
const form = document.getElementById("contact-form");
const statusEl = document.getElementById("contact-status");
const submitBtn = document.getElementById("contact-submit");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!FORMSPREE_ID || FORMSPREE_ID === "your_formspree_form_id") {
    statusEl.textContent =
      "Formspree is not configured yet. Set VITE_FORMSPREE_ID in frontend/.env";
    return;
  }

  submitBtn.disabled = true;
  statusEl.textContent = "Sending…";

  try {
    const response = await fetch(`https://formspree.io/f/${FORMSPREE_ID}`, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "application/json" },
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Formspree error (${response.status})`);
    }

    form.reset();
    statusEl.textContent = "Thanks — your message was sent.";
  } catch (error) {
    statusEl.textContent =
      error instanceof Error ? error.message : "Could not send the message.";
  } finally {
    submitBtn.disabled = false;
  }
});
