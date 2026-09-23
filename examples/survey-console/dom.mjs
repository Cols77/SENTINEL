export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (/^on/i.test(key)) throw new Error("Inline event handlers forbidden");
    if (value !== null && value !== false)
      node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children)
    node.append(
      child instanceof Node ? child : document.createTextNode(String(child)),
    );
  return node;
}
export function button(text, fn, attrs = {}) {
  const b = el("button", { type: "button", ...attrs }, [text]);
  if (fn) b.addEventListener("click", fn);
  return b;
}
export function replace(host, children) {
  const id = document.activeElement?.id;
  host.replaceChildren(...children);
  if (id) {
    const focused = document.getElementById(id);
    if (focused && host.contains(focused))
      focused.focus({ preventScroll: true });
  }
}
export const meta = (text) => el("p", { class: "meta" }, [text]);
