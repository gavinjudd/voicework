# Deliberate Chrome capture and visible Drive discovery

Load this folder as an unpacked Chrome extension: open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select `extension`. After updating these files, reload the extension. This package has syntax and unit validation; it has **not** been installed or exercised in the user's live Chrome session. Authentication, actual Drive markup, download responses, and permission prompts still require that browser check.

## Capture one excerpt

Select an excerpt on a page and right-click → **Review sample in Voicework**, or open the toolbar popup, choose **One excerpt**, and click **Read selected text from this tab**. Review or edit the title, text, context, and source. Download the JSON and import it in **Voicework → Samples**. You confirm authorship there.

Google Docs uses a canvas editor, so ordinary selection capture is not a reliable full-document reader. Copy/paste a passage or use the document workflow below.

## Discover Google Docs from the current tab

1. Open a Google Drive folder/search/list, or a single Google Doc, in the active Chrome tab.
2. Open the extension → **From Drive** → **Discover from current Drive / Docs tab**.
3. Review the candidate names. Choose up to **20** documents; none are preselected.
4. Optionally set a batch context, audience, and situation. These remain editable in Voicework. A document title is only a provisional context hint.
5. Choose **Allow Docs access & collect selected text**. Only this action requests optional access to `https://docs.google.com/*`. If allowed, a background task tries each selected document's TXT export using the browser's existing session.
6. Review each success or failure. **Download collected samples** exports only successfully collected, selected documents. The completion message states exactly how many selected documents were included and missing.
7. Import the JSON in **Voicework → Samples**. Every item starts pending; document access, ownership labels, and location never establish authorship.

Discovery inspects only visible, rendered document links and recognizable Google Docs rows in the current tab. Drive virtualizes lists and does not render every item. Scroll Drive and invoke discovery again to merge newly visible candidates. It does not search the Drive API, enumerate your account, read other tabs/history, traverse folders, or automatically scroll/crawl. Row detection depends on Drive's current markup and may miss rows; opening a Google Doc directly is the narrower fallback. Sheets, Slides, folders, hidden/unloaded rows, and non-document links are not imported. Session metadata is capped at 200 candidate documents.

## Authenticated export limits and reliable manual fallback

The direct collector uses the selected Google Doc's web TXT download route. This is a **best-effort browser-session bridge, not a Google Drive OAuth/API integration**. Your Google sign-in, sharing rights, organization rules, and disabled-download controls still apply. It never reads passwords, hidden tokens, cookies through an API, or another site's storage.

Only the Docs host can be granted. Fetch redirects are deliberately rejected: the extension does not request blanket `googleusercontent.com` or account-login access. If Google sends the export through another content domain, requires an interactive sign-in, returns HTML, or blocks access, that document gets an explicit failure with a manual fallback:

- Choose the candidate's **Open document** link and use **File → Download → Plain Text (.txt)**, or choose its **TXT download** link yourself.
- Reopen the popup → **From Drive** and attach that downloaded TXT to the matching candidate.
- Review the source association yourself; attaching a file cannot prove that it belongs to the displayed document.
- Keep the candidate selected and download the collected-samples JSON.

This path remains useful when automatic export cannot read the authenticated response. Opening documents/downloads uses ordinary visible links and does not bypass access restrictions.

Collection is sequential with 12 seconds per document and a 3-minute batch limit. Each read is capped at 1 MiB / 300,000 text characters, and collected output at 4 MiB. Documents over the 30,000-character sample limit are split into numbered parts with the same source URL; text is not silently truncated. Failed, empty, HTML, timed-out, interrupted, or over-limit documents produce explicit errors rather than placeholder samples. Rich text formatting does not survive a TXT export; missing formatting is not evidence of your preference.

## Privacy, persistence, and permissions

Base permissions: `activeTab`, `scripting`, `contextMenus`, and `storage`. The extension does not use `tabs`, `history`, `cookies`, `webRequest`, all-site host permissions, remote services, or localhost requests. `chrome.tabs.query` is limited to finding the current active tab under `activeTab`; it does not enumerate all browsing tabs.

Context-menu captures and discovery progress live in `chrome.storage.session`, not cloud storage. They expire on the next popup opening after 15 minutes of inactivity, or when the browser session ends. **Clear discovery** removes the saved candidates and text immediately. Completed results survive closing the popup while the background task continues. A browser-terminated task is detected on a later popup opening after four minutes; unresolved documents are marked failed while completed text is preserved.

Optional host access persists until removed. **Remove optional Docs access** revokes it; current-tab discovery remains available when explicitly invoked. Downloaded JSON/TXT files are separate local files containing private text; clear or retain them deliberately. Export source URLs are canonicalized without query strings or fragments.

## Validation and primary references

Run `node --test tests/test_browser_discovery.js` from the project root. The tests cover URL/type filtering, provenance schema, selection caps, text splitting, Unicode boundaries, rejected HTML/oversized content, failed-result exclusion, and permission boundaries. `node --check extension/popup.js` and `node --check extension/background.js` check syntax. These checks do not prove installed-extension operation.

- Chrome [optional permission requests](https://developer.chrome.com/docs/extensions/reference/api/permissions) must originate from a user action; [permission declarations](https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions) explain optional host access.
- Google [download instructions](https://support.google.com/docs/answer/49114?co=GENIE.Platform%3DDesktop&hl=en) provide the supported user-facing download workflow.
- Google's [Docs canvas announcement](https://workspaceupdates.googleblog.com/2021/05/Google-Docs-Canvas-Based-Rendering-Update.html) explains why DOM selection is not reliable document extraction.
