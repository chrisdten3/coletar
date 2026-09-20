// The toolbar button, and nothing else.
//
// A toolbar icon that does nothing when clicked reads as broken, and Chrome shows
// one for every installed extension whether or not `action` declares a popup. So
// clicking it opens the options page — the only screen this extension has, and the
// one a person looking for it actually wants.
//
// This worker deliberately holds no state, opens no connection and reads no page.
// The acquisition boundary lives in `content.js`, and the reason this file is four
// lines is so that it cannot quietly become somewhere that boundary is worked
// around: a background worker is exactly where "just read the conversation in the
// background" would be written, and there is nothing here to build that on.

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});
