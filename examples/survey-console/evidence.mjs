import { el, button, meta, replace } from "./dom.mjs";
export function supportingSightings(s, selection) {
  if (!selection) return [];
  if (selection.kind === "frame")
    return s.sightings.filter((x) => x.frame_id === selection.id);
  const fact =
    selection.kind === "class"
      ? s.counts.find((c) => c.class_name === selection.id)
      : s.objects.find((o) => o.id === selection.id);
  return fact
    ? s.sightings.filter((x) => fact.sighting_ids.includes(x.id))
    : [];
}
export function fitBox(cw, ch, iw, ih, [x1, y1, x2, y2]) {
  if (
    ![cw, ch, iw, ih].every((n) => Number.isFinite(n) && n > 0) ||
    ![x1, y1, x2, y2].every((n) => Number.isFinite(n) && n >= 0 && n <= 1) ||
    x2 < x1 ||
    y2 < y1
  )
    throw new Error("Invalid image bounds");
  const scale = Math.min(cw / iw, ch / ih),
    w = iw * scale,
    h = ih * scale;
  return {
    left: (cw - w) / 2 + x1 * w,
    top: (ch - h) / 2 + y1 * h,
    width: (x2 - x1) * w,
    height: (y2 - y1) * h,
  };
}
export function createEvidenceView(host, client) {
  let token = 0,
    url = null,
    observer = null,
    disposed = false,
    timer = null;
  function clearImage() {
    observer?.disconnect();
    observer = null;
    if (url) URL.revokeObjectURL(url);
    url = null;
    clearTimeout(timer);
    timer = null;
    host.querySelector(".image-stage")?.remove();
  }
  function clear() {
    token++;
    clearImage();
    replace(host, [
      el("h2", {}, ["Evidence & provenance"]),
      meta("Select a count, object or captured frame."),
    ]);
  }
  async function select(s, selection) {
    if (disposed) return;
    const selectedToken = ++token;
    clearImage();
    const rows = supportingSightings(s, selection);
    const ids =
      selection.kind === "frame"
        ? [selection.id]
        : [...new Set(rows.map((x) => x.frame_id))];
    const context = el("div", {}, [
      el("h2", {}, ["Evidence & provenance"]),
      meta(`${s.run_id} · ${selection.kind}: ${selection.id}`),
      meta(`${rows.length} supporting sightings · synthetic fixture evidence`),
    ]);
    const choices = el("div", { class: "evidence-choices" }),
      imageHost = el("div"),
      provenance = el("div");
    for (const row of rows)
      provenance.append(
        el("p", { class: "meta" }, [
          `${row.id} · ${row.frame_id} · ${row.provider} / ${row.model} · raw score ${row.raw_score ?? "—"} · ${row.score_semantics}`,
        ]),
      );
    replace(host, [context, choices, imageHost, provenance]);
    async function load(frameId) {
      const loadToken = ++token;
      clearImage();
      imageHost.replaceChildren();
      const f = s.frames.find((x) => x.id === frameId);
      const reason = (status, text) =>
        imageHost.replaceChildren(
          el("p", { class: "warning" }, [`${status} · ${text}`]),
        );
      if (!f) {
        reason("NOT_FOUND", "No supporting frame in this projection");
        return;
      }
      if (f.evidence !== "AVAILABLE") {
        reason(f.evidence, f.failure || "Evidence unavailable");
        return;
      }
      imageHost.append(meta("Loading evidence…"));
      try {
        let timeout;
        const expiration = new Promise((_, reject) => {
          timeout = setTimeout(
            () => reject(new Error("Evidence unavailable · timed out")),
            5000,
          );
          timer = timeout;
        });
        const result = await Promise.race([
          client.read_evidence(s.run_id, frameId),
          expiration,
        ]);
        if (disposed || loadToken !== token) return;
        if (result.run_id !== s.run_id || result.frame_id !== frameId)
          throw new Error("Evidence scope mismatch");
        if (result.status !== "AVAILABLE") {
          reason(result.status, result.reason);
          return;
        }
        if (
          !["image/png", "image/jpeg"].includes(result.media_type) ||
          !(result.bytes instanceof Uint8Array) ||
          !result.bytes.length ||
          result.bytes.length > 10485760
        )
          throw new Error("Unsupported or oversized evidence");
        const objectUrl = URL.createObjectURL(
          new Blob([result.bytes], { type: result.media_type }),
        );
        url = objectUrl;
        const img = el("img", {
          src: objectUrl,
          alt: `Synthetic fixture evidence · ${s.run_id} / ${frameId}`,
        });
        await Promise.race([img.decode(), expiration]);
        if (disposed || loadToken !== token) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        if (
          img.naturalWidth !== result.width ||
          img.naturalHeight !== result.height
        )
          throw new Error("Evidence dimensions disagree");
        const stage = el("div", { class: "image-stage" }, [img]);
        imageHost.replaceChildren(
          stage,
          meta("Synthetic image only; not a lecture-room photograph."),
        );
        const boxes = rows
          .filter((x) => x.frame_id === frameId && x.bbox)
          .map((row) => {
            const b = el(
              "div",
              {
                class: "sighting-box",
                "data-sighting-id": row.id,
                "aria-label": `${row.class_name} sighting ${row.id}`,
              },
              [el("span", {}, [row.id])],
            );
            stage.append(b);
            return { b, box: row.bbox };
          });
        const resize = () => {
          if (stage.clientWidth === 0 || stage.clientHeight === 0) return;
          for (const { b, box } of boxes) {
            const rect = fitBox(
              stage.clientWidth,
              stage.clientHeight,
              img.naturalWidth,
              img.naturalHeight,
              box,
            );
            for (const [key, n] of Object.entries(rect))
              b.style[key] = `${n}px`;
          }
        };
        observer = new ResizeObserver(resize);
        observer.observe(stage);
        resize();
      } catch (e) {
        if (!disposed && loadToken === token) {
          clearImage();
          reason("UNAVAILABLE", e.message);
        }
      } finally {
        if (loadToken === token) {
          clearTimeout(timer);
          timer = null;
        }
      }
    }
    for (const id of ids)
      choices.append(
        button(id, () => load(id), { id: `evidence-frame-${id}` }),
      );
    if (!ids.length) {
      imageHost.append(meta("No supporting frames for this selection."));
      return;
    }
    if (selectedToken !== token || disposed) return;
    await load(ids[0]);
  }
  clear();
  return {
    select,
    clear,
    dispose() {
      disposed = true;
      clear();
    },
  };
}
