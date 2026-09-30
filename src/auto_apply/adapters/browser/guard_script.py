"""SubmitGuard 가 페이지에 심는·실행하는 고정 스크립트 (§A4 L3·L5, §A5 ready_for_review).

하네스 코드만 실행한다 — 에이전트가 JS 를 넘길 통로는 없다(§A4 L1).
"""

import json

from auto_apply.adapters.browser.snapshot_script import HELPERS

GUARD_KEY = "__autoApplySubmitGuard"

# 모든 문서(프레임 포함)가 만들어질 때 페이지 스크립트보다 먼저 돈다 (add_init_script).
# submit 이벤트(암묵 제출·type=submit 클릭·requestSubmit 이 내는 것)를 캡처 단계 맨 앞에서 끊고,
# 이벤트를 내지 않는 form.submit() 과 requestSubmit() 자체도 막는다. 막은 것은 기록해 두고
# 하네스가 토큰으로 수거한다 — 페이지는 토큰을 모르니 상태를 끄거나 기록을 지울 수 없다.
# 페이지가 다른 프레임의 원본 함수를 빌려 오는 식으로 이 층을 비껴가도
# 네트워크 층(route)이 문서 POST 를 막는다.
_GUARD_TEMPLATE = r"""
(() => {
  const KEY = __KEY__;
  if (Object.prototype.hasOwnProperty.call(window, KEY)) return;
  const TOKEN = __TOKEN__;
  let armed = true;
  let step = null;  // 단계 이동(D17)으로 허용한 버튼 — 그 버튼이 낸 submit 한 번만 통과한다
  const events = [];
  const note = (what) => { if (events.length < 100) events.push(what); };
  const F = window.HTMLFormElement && window.HTMLFormElement.prototype;
  if (F) {
    const submit = F.submit, requestSubmit = F.requestSubmit;
    F.submit = function () {
      if (armed) { note("form.submit"); return; }
      return submit.call(this);
    };
    if (requestSubmit) {
      F.requestSubmit = function (...args) {
        if (armed) { note("form.requestSubmit"); return; }
        return requestSubmit.apply(this, args);
      };
    }
  }
  window.addEventListener("submit", (e) => {
    if (!armed) return;
    if (step !== null && e.submitter === step && e.target === step.form) { step = null; return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    note("submit");
  }, true);
  Object.defineProperty(window, KEY, {configurable: false, enumerable: false, writable: false,
    value: (token, on, allow) => {
      if (token !== TOKEN) return null;
      if (allow !== undefined) { step = allow; return []; }
      step = null;  // 허용은 그 창 안에서만 — 창을 닫는 수거(on=null)·켜고 끄기가 지운다
      if (typeof on === "boolean") armed = on;
      return events.splice(0, events.length);
    }});
})();
"""


def guard_script(token: str) -> str:
    return _GUARD_TEMPLATE.replace("__KEY__", json.dumps(GUARD_KEY)).replace(
        "__TOKEN__", json.dumps(token)
    )


# STEP 창(D17): 클릭할 요소가 속한 제출 버튼의 submit 한 번을 허용한다. 요소의 프레임에서 돈다.
# 허용하지 못했으면(가드 없는 프레임) 그 submit 은 막힌다 — 닫힌 쪽.
ALLOW_STEP = """(el, [key, token]) => {
  let b = el;
  while (b && b.tagName !== "BUTTON" && b.tagName !== "INPUT") b = b.parentElement;
  const f = window[key];
  return typeof f === "function" && !!b && Array.isArray(f(token, null, b));
}"""

# 프레임의 가드를 켜고/끄고(`on`=true/false) 막은 기록을 수거한다(`on`=null).
# 가드가 없는 프레임은 null.
CONTROL = """([key, token, on]) => {
  const f = window[key];
  return typeof f === "function" ? f(token, on) : null;
}"""

# 켤 때 프레임마다 가드가 **우리 것**인지 확인하고 켠다. 가드가 꺼진 동안(사람 로그인) 열린 문서는
# init script 없이 떠서, 페이지가 가드 이름을 먼저 차지했을 수 있다 — 가짜는 토큰을 모르니
# 엉뚱한 토큰(decoy)엔 null, 진짜 토큰엔 배열을 돌려주지 못한다.
VERIFY = """([key, token, decoy]) => {
  const f = window[key];
  if (typeof f !== "function") return false;
  return f(decoy, null) === null && Array.isArray(f(token, true));
}"""

# 클릭 분류(§A4 L2) 기술자 — 요소와, 클릭이 닿는 조작 가능한 조상·라벨이 가리키는
# 컨트롤(가장 안쪽부터).
DESCRIBE_CLICK = (
    "(target) => {"
    + HELPERS
    + r"""
  const isSubmitBtn = (c) => (c.tagName === "BUTTON" && c.type === "submit") ||
    (c.tagName === "INPUT" && (c.type === "submit" || c.type === "image"));
  const describe = (e) => {
    const tag = e.tagName.toLowerCase();
    const role = roleOf(e);
    const owner = ("form" in e && e.form) || e.closest("form");
    const def = owner ? [...owner.elements].find(isSubmitBtn) : null;
    return {tag: tag, type: e.getAttribute("type"), role: role || null, name: nameOf(e, role),
      text: clean(e.innerText || e.textContent, 500), in_form: !!owner,
      is_form_default_button: !!def && def === e,
      in_dialog: !!e.closest("dialog, [role=dialog], [role=alertdialog]"),
      href: tag === "a" ? e.getAttribute("href") : null};
  };
  const ACTIVATES = "button, a[href], summary, [role=button], [role=link], [role=menuitem], " +
    "[role=option], [role=tab], [onclick]";
  const out = [describe(target)];
  const seen = new Set([target]);
  const add = (e) => {
    if (e && !seen.has(e) && out.length < 12) { seen.add(e); out.push(describe(e)); }
  };
  for (let e = target; e; e = e.parentElement) {
    if (e !== target && e.matches(ACTIVATES)) add(e);
    if (e.tagName === "LABEL") add(e.control);  // 라벨을 누르면 그 컨트롤이 눌린다
  }
  return out;
}"""
)

# L5·D17 관찰 — 보이는 글자, 편집 가능한 입력칸 수(체크박스 제외, 파일 입력은 숨겨도 센다),
# 보이는 폼 제출 버튼 수, `aria-current="step"` 이 진행 목록의 몇 번째인가.
READ_PAGE = r"""() => {
  const SKIP = new Set(["hidden", "checkbox", "submit", "button", "reset", "image"]);
  const shown = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
  let inputs = 0, submitters = 0;
  for (const e of document.querySelectorAll("button, input[type=submit], input[type=image]")) {
    if (e.type === "submit" || e.type === "image") {
      if (e.form && !e.disabled && shown(e)) submitters++;
    }
  }
  const fields = "input, textarea, select, [contenteditable=''], [contenteditable=true]";
  for (const e of document.querySelectorAll(fields)) {
    if (e.disabled || e.readOnly) continue;
    const type = e.tagName === "INPUT" ? e.type : "";
    if (SKIP.has(type)) continue;
    if (type === "file" || shown(e)) inputs++;
  }
  const progress = [];
  for (const e of [...document.querySelectorAll('[aria-current="step"]')].slice(0, 10)) {
    const item = e.closest("li") || e;
    const items = item.parentElement ? [...item.parentElement.children] : [item];
    progress.push([items.indexOf(item) + 1, items.length]);
  }
  return {text: document.body ? document.body.innerText : "", inputs: inputs,
    submitters: submitters, progress: progress};
}"""

# ready_for_review — 승인 뒤(§A4 L6) 같은 요소인지 다시 찾을 선택자 후보와 문서 좌표.
TARGET = r"""(el) => {
  const doc = el.ownerDocument;
  const win = doc.defaultView;
  const only = (sel) => {
    try { const all = doc.querySelectorAll(sel); return all.length === 1 && all[0] === el; }
    catch (e) { return false; }
  };
  const quote = (s) => '"' + s.replace(/["\\]/g, "\\$&") + '"';
  const tag = el.tagName.toLowerCase();
  const sels = [];
  if (el.id && only("#" + CSS.escape(el.id))) sels.push("#" + CSS.escape(el.id));
  const name = el.getAttribute("name");
  const byName = name ? tag + "[name=" + quote(name) + "]" : "";
  if (byName && only(byName)) sels.push(byName);
  const parts = [];
  for (let e = el; e && e.nodeType === 1 && e !== doc.documentElement; e = e.parentElement) {
    if (e !== el && e.id && doc.querySelectorAll("#" + CSS.escape(e.id)).length === 1) {
      parts.unshift("#" + CSS.escape(e.id));
      break;
    }
    let i = 1;
    for (let s = e.previousElementSibling; s; s = s.previousElementSibling) {
      if (s.tagName === e.tagName) i++;
    }
    parts.unshift(e.tagName.toLowerCase() + ":nth-of-type(" + i + ")");
  }
  const path = parts.join(" > ");
  if (path && !sels.includes(path) && only(path)) sels.push(path);
  const r = el.getBoundingClientRect();
  return {selectors: sels,
    box: {x: r.x + win.scrollX, y: r.y + win.scrollY, width: r.width, height: r.height}};
}"""
