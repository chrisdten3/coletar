// Pure turn helpers are shared by the content script and synthetic adapter tests.
const ColetaBridge = (() => {
  const MARKER = "— coleta —";
  const MARKERS = [MARKER, "— coletar —"];
  const strip = (text) => {
    for (const marker of MARKERS) {
      if (text.includes(marker)) return text.split(marker).pop().trim();
    }
    return text.trim();
  };
  // Rich-text editors render paragraph boundaries as one or two newlines and
  // use NBSP for visible spaces. Preserve words and indentation when comparing.
  const sameText = (left, right) => {
    const normalize = (text) => text.replace(/\r\n?/g, "\n")
      .replace(/\u00a0/g, " ").replace(/\n{2,}/g, "\n").trim();
    return normalize(left) === normalize(right);
  };
  const autoEnabled = (settings) => Boolean(settings.automatic && settings.automaticConsent);
  const sendsOnEnter = (event) => event.key === "Enter" && !event.shiftKey &&
    !event.ctrlKey && !event.altKey && !event.metaKey && !event.isComposing && !event.repeat;
  const augment = (text, data) => data?.results?.length && data.prompt_block
    ? `${data.prompt_block}\n\n${MARKER}\n\n${text}` : text;
  // Cancel the result rather than letting a late request change a newer draft.
  const bounded = (promise, milliseconds, fallback = null) => new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), milliseconds);
    Promise.resolve(promise).then(
      (value) => { clearTimeout(timer); resolve(value); },
      () => { clearTimeout(timer); resolve(fallback); },
    );
  });
  return { MARKER, strip, sameText, autoEnabled, sendsOnEnter, augment, bounded };
})();
if (typeof module !== "undefined") module.exports = ColetaBridge;
