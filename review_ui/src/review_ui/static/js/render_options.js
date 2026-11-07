// "Imagem e som" on the job page (LIVESTACK-50): Girar, Enquadramento, Sem áudio.
//
// Keeps the page in step with the form while the reviewer changes it,
// before anything is saved:
//   * the orientation line and whether "Enquadramento" applies at all
//     (a picture that is 16:9 after the turn renders as before);
//   * the player turns with "Girar" (CSS), so the reviewer sees the clip's
//     way up; the browser's own controls are hidden while it is turned
//     (they would turn too), the keyboard and the cut bar still work;
//   * with "Cortar", a band over the player shows what goes into the clip;
//   * with "Sem áudio", the player is muted: the reviewer hears what goes out.
// The rules mirror shared/framing.py; test_render_options_page.py runs the
// exported functions in Node against the same cases.
import { t } from "./text.js";

const OUTPUT_WIDTH = 1920;
const OUTPUT_HEIGHT = 1080;

// (width, height) after a clockwise turn: a quarter turn swaps them.
export function turnedSize(width, height, rotation) {
  return rotation % 180 === 90 ? [height, width] : [width, height];
}

// True when the old render (scale=-2:1080) gives exactly 1920×1080: 16:9.
export function fillsFrameLikeBefore(width, height) {
  return 2 * Math.round((width * OUTPUT_HEIGHT) / (2 * height)) === OUTPUT_WIDTH;
}

// The catalog key of the orientation line for a turned picture.
export function orientationKey(width, height) {
  if (!width || !height) return "script.orientation.unknown";
  if (height > width) return "script.orientation.vertical";
  if (fillsFrameLikeBefore(width, height)) return "script.orientation.horizontal";
  return "script.orientation.other";
}

// The "Cortar" band as fractions (0..1) of the turned picture: a full-width
// band sliding down for a picture taller than 16:9, a full-height band
// sliding right for a wider one. `position`: 0 top/left … 1 bottom/right.
export function cropBandFractions(width, height, position) {
  if (width * OUTPUT_HEIGHT < height * OUTPUT_WIDTH) {
    const bandHeight = Math.min(1, (width * 9) / 16 / height);
    return { left: 0, top: (1 - bandHeight) * position, width: 1, height: bandHeight };
  }
  const bandWidth = Math.min(1, (height * 16) / 9 / width);
  return { left: (1 - bandWidth) * position, top: 0, width: bandWidth, height: 1 };
}

export function setupRenderOptions({ form, video }) {
  const box = document.getElementById("render-options");
  if (!box) return;
  const note = document.getElementById("orientation-note");
  const framingBox = document.getElementById("framing-box");
  const cropBox = document.getElementById("crop-position-box");
  const band = document.getElementById("crop-band");
  const stage = document.getElementById("player-stage");
  // The raw's size as players show it (Job.display_width/height); while it
  // is unknown, the proxy's own size has the same shape once it loaded.
  const savedWidth = Number(box.dataset.displayWidth) || 0;
  const savedHeight = Number(box.dataset.displayHeight) || 0;

  function pictureSize() {
    if (savedWidth && savedHeight) return [savedWidth, savedHeight];
    if (video && video.videoWidth && video.videoHeight) return [video.videoWidth, video.videoHeight];
    return [0, 0];
  }

  function choices() {
    return {
      rotation: Number(form.elements.rotation.value) || 0,
      framing: form.elements.framing?.value || "blurred",
      position: Number(form.elements.crop_position?.value ?? 0.5),
      noAudio: form.elements.no_audio.checked,
    };
  }

  // Turn the player and place the crop band. Sizes are in CSS pixels of the
  // stage; the video element keeps its 16:9 box and shows the proxy inside it.
  function layoutPlayer({ rotation, framing, position }, applies) {
    if (!video || !stage) return;
    const stageWidth = video.clientWidth;
    const stageHeight = video.clientHeight;
    const proxyWidth = video.videoWidth;
    const proxyHeight = video.videoHeight;
    if (!stageWidth || !stageHeight || !proxyWidth || !proxyHeight) {
      if (band) band.hidden = true;
      return;
    }
    // The proxy as the browser draws it unturned (contained in the box)...
    const contain = Math.min(stageWidth / proxyWidth, stageHeight / proxyHeight);
    const drawnWidth = proxyWidth * contain;
    const drawnHeight = proxyHeight * contain;
    // ...then turned, and scaled so the turned picture fits the box again.
    const quarter = rotation % 180 === 90;
    const scale = quarter
      ? Math.min(stageWidth / drawnHeight, stageHeight / drawnWidth)
      : 1;
    video.style.transform = rotation ? `rotate(${rotation}deg) scale(${scale})` : "";
    video.controls = rotation === 0;
    if (!band) return;
    const showBand = applies && framing === "crop";
    band.hidden = !showBand;
    if (!showBand) return;
    const pictureWidth = (quarter ? drawnHeight : drawnWidth) * scale;
    const pictureHeight = (quarter ? drawnWidth : drawnHeight) * scale;
    const [turnedWidth, turnedHeight] = turnedSize(proxyWidth, proxyHeight, rotation);
    const fractions = cropBandFractions(turnedWidth, turnedHeight, position);
    const left = (stageWidth - pictureWidth) / 2 + fractions.left * pictureWidth;
    const top = (stageHeight - pictureHeight) / 2 + fractions.top * pictureHeight;
    band.style.left = `${left}px`;
    band.style.top = `${top}px`;
    band.style.width = `${fractions.width * pictureWidth}px`;
    band.style.height = `${fractions.height * pictureHeight}px`;
  }

  function update() {
    const current = choices();
    const [width, height] = pictureSize();
    const [turnedWidth, turnedHeight] = turnedSize(width, height, current.rotation);
    note.textContent = t(orientationKey(turnedWidth, turnedHeight), {
      width: turnedWidth,
      height: turnedHeight,
    });
    // Unknown size: keep the choices visible; the worker decides with the raw.
    const applies = !width || !fillsFrameLikeBefore(turnedWidth, turnedHeight);
    framingBox.hidden = !applies;
    if (cropBox) cropBox.hidden = current.framing !== "crop";
    layoutPlayer(current, applies);
  }

  for (const name of ["rotation", "framing", "crop_position"]) {
    const field = form.elements[name];
    const fields = field instanceof RadioNodeList ? [...field] : [field];
    for (const element of fields) element?.addEventListener("input", update);
  }
  // The player says what goes out: muted while "Sem áudio" is ticked.
  form.elements.no_audio.addEventListener("change", () => {
    if (video) video.muted = form.elements.no_audio.checked;
  });
  if (video) {
    video.muted = form.elements.no_audio.checked;
    video.addEventListener("loadedmetadata", update);
    new ResizeObserver(() => layoutPlayer(choices(), !framingBox.hidden)).observe(video);
  }
  update();
}
