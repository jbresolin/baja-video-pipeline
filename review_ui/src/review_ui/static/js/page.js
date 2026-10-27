// Runs on every page: local times and a message left over from a reload.
import { localizeTimes, showStoredFlash } from "./common.js";

localizeTimes(document);
showStoredFlash();
