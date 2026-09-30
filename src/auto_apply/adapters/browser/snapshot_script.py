"""snapshot 이 프레임마다 실행하는 DOM 수집 스크립트 (§A5).

하네스 코드가 실행하는 고정 스크립트다 — 에이전트가 JS 를 넘길 통로는 없다(§A4 L1).
요소에 표시 속성을 심지 않고 요소 핸들을 Python 쪽 표에 쥔다: 페이지가 ref 를 위조해 다른
요소를 가리키게 할 수 없다. 비밀번호·인증 코드 칸은 `.value` 를 읽지 않는다(절대 규칙 3) —
규칙은 `domain/page_elements.py` 와 같고 `contracts/page.py` 가 한 번 더 버린다.
"""

MAX_NODES = 1500

COLLECT = r"""
(maxNodes) => {
  const SECRET_AC = ["current-password", "new-password", "one-time-code"];
  const ROLE_OK = new Set(["button", "link", "checkbox", "radio", "textbox", "combobox", "listbox",
    "option", "switch", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "searchbox",
    "spinbutton", "slider", "treeitem"]);
  const TEXT_TAGS = new Set(["P", "LI", "LEGEND", "TD", "TH", "DT", "DD", "CAPTION", "SPAN",
    "DIV"]);
  const CTX_ROLES = new Set(["status", "alert", "dialog", "alertdialog"]);
  const clean = (s, n) => (s || "").replace(/\s+/g, " ").trim().slice(0, n || 200);
  const inputType = (el) => (el.getAttribute("type") || "text").trim().toLowerCase();
  const isSecret = (el) => el.tagName === "INPUT" && (inputType(el) === "password" ||
    (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/)
      .some((t) => SECRET_AC.includes(t)));
  const visible = (el) => el.checkVisibility
    ? el.checkVisibility({checkVisibilityCSS: true}) : !!(el.offsetWidth || el.offsetHeight);

  function roleOf(el) {
    const explicit = (el.getAttribute("role") || "").trim().split(/\s+/)[0].toLowerCase();
    if (explicit) return explicit;
    const t = el.tagName;
    if (t === "A") return el.hasAttribute("href") ? "link" : "";
    if (t === "BUTTON") return "button";
    if (t === "SELECT") return el.multiple || el.size > 1 ? "listbox" : "combobox";
    if (t === "TEXTAREA") return "textbox";
    if (/^H[1-6]$/.test(t)) return "heading";
    if (t === "DIALOG") return "dialog";
    if (t === "INPUT") {
      const ty = inputType(el);
      if (["button", "submit", "reset", "image"].includes(ty)) return "button";
      if (ty === "checkbox" || ty === "radio") return ty;
      if (ty === "file") return "file";
      if (ty === "hidden") return "";
      if (ty === "range") return "slider";
      if (ty === "number") return "spinbutton";
      if (ty === "search") return "searchbox";
      return "textbox";
    }
    const parent = el.parentElement;
    const editRoot = el.isContentEditable && parent && !parent.isContentEditable;
    if (editRoot) return "textbox";
    return "";
  }

  function labelText(label) {
    const copy = label.cloneNode(true);
    copy.querySelectorAll("input, select, textarea, button").forEach((c) => c.remove());
    return copy.textContent;
  }

  function nameOf(el, role) {
    const doc = el.ownerDocument;
    const by = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map((id) => doc.getElementById(id)).filter(Boolean).map((n) => n.textContent).join(" ");
    if (clean(by)) return clean(by);
    if (clean(el.getAttribute("aria-label"))) return clean(el.getAttribute("aria-label"));
    if (el.labels && el.labels.length) {
      const s = clean([...el.labels].map(labelText).join(" "));
      if (s) return s;
    }
    if (el.tagName === "INPUT" && ["button", "submit", "reset"].includes(inputType(el))) {
      return clean(el.value || el.getAttribute("value") || inputType(el));
    }
    if (el.tagName === "INPUT" && inputType(el) === "image") return clean(el.getAttribute("alt"));
    if (["button", "link", "heading", "tab", "menuitem", "option", "treeitem", "dialog",
         "alertdialog", "checkbox", "radio", "switch"].includes(role) && el.tagName !== "INPUT") {
      return clean(el.innerText || el.textContent);
    }
    if (el.tagName === "FIELDSET") {
      const legend = el.querySelector("legend");
      if (legend) return clean(legend.textContent);
    }
    return clean(el.getAttribute("placeholder") || el.getAttribute("title") ||
      el.getAttribute("alt"));
  }

  const els = [], infos = [];
  let truncated = false;
  const textShown = new Set();
  const underText = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) if (textShown.has(p)) return true;
    return false;
  };
  const walker = document.createTreeWalker(document.body || document.documentElement,
    NodeFilter.SHOW_ELEMENT);
  for (let el = walker.currentNode; el; el = walker.nextNode()) {
    if (infos.length >= maxNodes) { truncated = true; break; }
    const role = roleOf(el);
    const isFile = el.tagName === "INPUT" && inputType(el) === "file";
    // 숨긴 파일 입력은 흔하다(버튼이 대신 연다) — 업로드는 숨은 입력에 한다.
    const shown = visible(el);
    if (!shown && !isFile) continue;
    const interactive = isFile || ROLE_OK.has(role);
    if (interactive) {
      const info = {role: role, name: nameOf(el, role), tag: el.tagName.toLowerCase(),
        input_type: el.tagName === "INPUT" ? el.getAttribute("type") : null,
        autocomplete: el.getAttribute("autocomplete"), disabled: !!el.disabled ||
        el.getAttribute("aria-disabled") === "true", required: !!el.required, hidden: !shown};
      const secret = isSecret(el);
      info.secret = secret;
      if (el.tagName === "SELECT") {
        info.options = [...el.options].slice(0, 100).map((o) => clean(o.label || o.text));
        info.value = clean([...el.selectedOptions].map((o) => o.label || o.text).join(", "));
      } else if (el.tagName === "INPUT" && ["checkbox", "radio"].includes(inputType(el))) {
        info.checked = el.checked;
      } else if (el.getAttribute("aria-checked") !== null) {
        info.checked = el.getAttribute("aria-checked") === "true";
      } else if (!secret && !isFile && (el.tagName === "TEXTAREA" || role === "textbox" ||
                 role === "searchbox" || role === "spinbutton" || role === "combobox")) {
        // 값은 접지 않는다 — 여러 줄 답변을 에이전트가 그대로 확인할 수 있게.
        info.value = (el.isContentEditable ? el.innerText : el.value).slice(0, 2000);
      }
      els.push(el); infos.push(info);
      continue;
    }
    const ctx = CTX_ROLES.has(role) || role === "heading";
    if (!ctx && !TEXT_TAGS.has(el.tagName)) continue;
    if (underText(el)) continue;
    // 입력 칸을 품은 덩어리는 칸의 라벨이 이미 설명한다 — 본문으로 되풀이하지 않는다.
    if (!ctx && el.querySelector("input, select, textarea, button, a[href], [role]")) continue;
    const text = clean(el.innerText || el.textContent);
    if (!text && !ctx) continue;
    if (el.tagName === "SPAN" || el.tagName === "DIV") {
      const inline = ["B", "I", "EM", "STRONG", "BR", "SMALL"];
      if ([...el.children].some((c) => !inline.includes(c.tagName))) continue;
    }
    textShown.add(el);
    const level = role === "heading"
      ? Number(el.getAttribute("aria-level") || el.tagName.slice(1)) || null : null;
    els.push(null);
    infos.push({role: ctx ? role : "text", name: text, tag: el.tagName.toLowerCase(),
      level: level});
  }
  return {els: els, infos: infos, truncated: truncated, title: document.title};
}
"""

# 동작 직전에 대상의 **지금** 모양을 다시 읽는다 — snapshot 뒤 type 이 바뀐 칸을 잡는다.
DESCRIBE = r"""
(el) => ({tag: el.tagName.toLowerCase(), type: el.getAttribute("type"),
  autocomplete: el.getAttribute("autocomplete"), editable: el.isContentEditable,
  connected: el.isConnected})
"""

SCROLL_PAGE = "(down) => window.scrollBy(0, (down ? 1 : -1) * Math.round(window.innerHeight * 0.8))"

# select: 라벨이 먼저, 없으면 value — 에이전트는 snapshot 의 라벨을 보고 고른다.
FIND_OPTION = """(el, o) => {
  const opts = [...el.options];
  const byLabel = opts.findIndex((x) => (x.label || x.text).trim() === o.trim());
  return byLabel >= 0 ? byLabel : opts.findIndex((x) => x.value === o);
}"""
SELECTED_LABELS = (
    "(el) => [...el.selectedOptions].map((x) => (x.label || x.text).trim()).join(', ')"
)
