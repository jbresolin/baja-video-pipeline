// Fleet page: replace the whole body with a fresh copy every few seconds.
import { localizeTimes } from "./common.js";
import { startPolling } from "./poll.js";

const body = document.getElementById("fleet-body");

startPolling(body, (html) => {
  body.innerHTML = html;
  localizeTimes(body);
});
