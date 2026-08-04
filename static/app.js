async function post(path) {
  const res = await fetch(path, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

function showBanner(message, isError = false) {
  const el = document.getElementById("banner");
  if (!el) return;
  el.hidden = !message;
  el.textContent = message || "";
  el.classList.toggle("error", Boolean(isError));
}

function setBusy(busy) {
  for (const id of ["refresh-btn", "alert-btn"]) {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = busy;
  }
}

document.getElementById("refresh-btn")?.addEventListener("click", async () => {
  showBanner("");
  setBusy(true);
  try {
    await post("/api/collect");
    window.location.reload();
  } catch (err) {
    showBanner(err.message || "Refresh failed", true);
    setBusy(false);
  }
});

document.getElementById("alert-btn")?.addEventListener("click", async () => {
  showBanner("");
  setBusy(true);
  try {
    const data = await post("/api/alerts/test");
    const delivered = (data.results || [])
      .filter((r) => r.ok)
      .map((r) => r.channel)
      .join(", ");
    showBanner(
      data.ok
        ? `Test alert delivered via ${delivered}`
        : "Channels configured but delivery failed — check credentials."
    );
  } catch (err) {
    showBanner(err.message || "Alert test failed", true);
  } finally {
    setBusy(false);
  }
});

setInterval(() => {
  window.location.reload();
}, 60_000);
