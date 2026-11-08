# Review UI (LIVESTACK-27, fleet view LIVESTACK-26)

The web app reviewers use at `https://<PIPELINE_DOMAIN>/review/`. It runs as
its own service (design §16) and keeps **no data of its own**. Every page is
built from the orchestrator's HTTP API, called with the signed-in user's
session: the `/review/login` page signs in through the orchestrator and keeps
the token in the `bvp_session` cookie (httpOnly, Secure, SameSite=Lax), which
every page sends on as `Authorization: Bearer …`. The orchestrator checks it
and records the reviewer in `Job.reviewed_by`. Admins also get the pages
under `/review/admin/`: users, contacts, provas, teams, enrollment,
workers, settings. The whole UI is in pt-BR (see "Language" below).

```
browser ──443──▶ Caddy ──/review*──▶ review-ui:8001 ──HTTP API──▶ orchestrator:8000
                   ├──/proxies/*, /thumbs/*, /previews/* served by Caddy itself after forward_auth to /auth/media
                   └──/requested-clips/* passed to the orchestrator, which checks the cookie and sends the clip
```

Caddy routes only `/review*` to this service; the orchestrator's API is not
public. So the page scripts call the UI's own JSON endpoints under
`/review/api/`, and the UI passes those calls on to the orchestrator.

## Pages

| Path | What |
| --- | --- |
| `/review/` | Queue: awaiting review + failed by default, in filming order (`recorded_at`, LIVESTACK-45; each row says when it was filmed, marked "envio"/"chegada" when that is only the send or arrival time, and "recebido 14:05"; a "💬 n" badge when the sender wrote about the video, LIVESTACK-49); filters by state, prova, "Enviado por" (`?sender=`, LIVESTACK-51), low-res, "Clipes desatualizados" (`?outdated=1`: clips that no longer match their review, badge "desatualizado"); "👤 Pedro" on a row someone else has open, "duplicado" on a copy; auto-refresh every 5 s; `↑↓`/`j k` + `Enter` |
| `/review/jobs/{id}` | Proxy player, cut in/out marks, fields, actions, status strip, event log from `GET /jobs/{id}/events` (refreshes every 5 s; if the log cannot be read the page still works and says so) |
| `/review/clips` | Delivery manifest, newest first, prova filter, copyable studio path; a clip that no longer matches its review is marked "desatualizado" with the differences on expand; "Ver clipe" plays the clip's 480p preview in a dialog (or "sem prévia", the reason on hover) and "Baixar clipe" on every row (LIVESTACK-52) |
| `/review/fleet` | Workers (role, online, heartbeat age, held raws, storage vs cap, stats, leases, last 5 events from `GET /events?worker_id=…&limit=5`), VPS disk, jobs per state; refreshes every 10 s |
| `/healthz` | Container health check (not routed by Caddy) |

Job page keys: `Space`/`k` play, `i`/`o` set in/out, `Shift+i`/`o` jump to
them, `←`/`→` one frame, `Shift+←`/`→` one second, `-`/`=` nudge the last
mark by a frame, `p` play the cut, `Ctrl+S` save, `Ctrl+Enter`
ready/reprocess/retry, `?` shows the list. A frame is `1 / Job.frame_rate`
seconds (the proxy's fps, measured by the holder that uploaded it), or
1/30 s when the job has no frame rate yet; the server puts the value in
`data-frame-rate` on the page and the shortcut list shows it.

**Passagem help** (LIVESTACK-45). As soon as the form has a prova and a
car (saved or not), the panel "Outros vídeos de <prova>, carro <n>" lists
the car's other videos in that prova (`GET /review/car-videos`) in
filming order, with this one placed among them: thumbnail, recording
time, passagem, state, link. Below it, "Passagens já registradas: …" and
a suggestion with a "Usar passagem N" button (the field is never filled
otherwise). Warnings for the passagem typed (number already used, filming
order broken) show under the field and above the buttons; "Pronto" and
"Salvar e reprocessar" ask to confirm them, never block.
`static/js/passagem_help.js` refreshes the panel in place through
`GET /review/api/jobs/{id}/passagem-help` (debounced, no reload); the
rules are in `passagem_help_view.py`.

**Watch and download the clip** (LIVESTACK-52). Once a job has a clip,
the job page has a closed "Ver clipe" block (`_clip_preview.html`): a
player for the newest clip's 480p preview (`Job.clip_preview.url`, served
by Caddy under `/previews/` like proxies, `preload="none"`), or the reason
there is none (`clip_preview.state.*`: being made, switched off, a worker
before 0.7.0, evicted, missing) and a pointer to "Baixar clipe". "Baixar
clipe" (job page and every clips-page row, `static/js/clip_review.js`)
works like "Baixar original": `POST /review/api/jobs/{id}/clip-download`,
then "Preparando o clipe…" polled every 5 s until it is a link to
`/requested-clips/<job>/<clip name>`. It replaces the old direct link to
an outbox clip (`/clip-downloads/`, still served by Caddy): an outbox clip
is ready at once anyway. Configurações has "Gerar prévia dos clipes" (a
hidden `clip_previews_enabled_shown` field tells an unticked box from an
older form) and the two times the VPS keeps a requested clip.

**Sender's messages** (LIVESTACK-49). The header shows "Gravado …",
"Recebido …" and "Enviado por …" side by side. Under the status strip,
"Mensagens de <remetente> (n)" (`GET /jobs/{id}/sender-messages`, shaped
by `sender_messages_view.py`, template `_sender_messages.html`) lists the
video's caption, replies to it ("resposta a este vídeo"), what the sender
wrote after it until their next video, and, folded, "Antes deste vídeo".
Media show as "(áudio)", "(imagem)"; edits say "editada", deletions
"apagada". Closed by default, open when one of them replies to this video;
plain `<details>`, no script. If the call fails the page says so and works.
The orchestrator decides which messages belong where; the UI only shows them.

**Edits after delivery** (LIVESTACK-43). Once a clip exists (claimed,
uploaded, delivered, done), the primary button is **"Salvar e
reprocessar"**: the `reprocess` action, which saves the fields in the same
request and renders the next clip version (`…-v2.mp4` on the box; the
version on air is never written over). Plain "Salvar" stays as a secondary
button; on such a job its confirmation says the clip on air does not
change until reprocessed, and the page reloads. Whenever the newest clip
no longer matches the review (`Job.clip_inputs.changed`, computed by the
orchestrator, including a name choice changed in Settings), the status
strip shows a warning banner with one sentence per difference
(`clip_inputs_view.py`), e.g. "O clipe no ar usa corte 0:03–0:12; a
revisão agora diz 0:05–0:12." It is part of the polled panel, so it goes
away by itself when the new version arrives.

**Imagem e som** (LIVESTACK-50). The job page says the video's shape
("Vídeo na vertical (1080×1920)."; vertical videos are also marked in the
queue and the header) and has a box under "Mostrar o número da
passagem": **Girar** (0°/90°/180°/270°, for a phone whose rotation note
is wrong; the player turns with it), **Enquadramento** (desfocado, barras
pretas, cortar with the band's place; hidden for a 16:9 picture, where it
changes nothing; "cortar" draws the band over the player) and **Sem
áudio** (the player is muted while ticked). `static/js/render_options.js`
keeps all of that in step with the fields before saving, with the same
rules as `shared/framing.py` (tested in Node); the server side is
`render_options_view.py`. A job waiting because no holder runs 0.4.0 says
so; Frota shows what each worker version can render; Configurações holds
the bot's reply to vertical videos.

**Official pass results** (LIVESTACK-47). For a prova in the
pass-results configuration (`Job.pass_result.configured`), the job page has
**"Mostrar resultado da passagem"** under "Mostrar o número da passagem",
ticked as `pass_result.show` says (the reviewer's choice, else the prova's
default), and one line under it: what will be burned in ("Na tarja: 66 s ·
4 cones → 86 s"), "Não será gravado (resultado: …)" when unticked, or why
there is no result yet (one sentence per `PassResultProblem`, the
orchestrator's details after it in `lang="en"`). When the results API is
failing: "API de resultados inacessível desde 14:02 (último resultado de
13:58)". Nothing blocks "Pronto". `js/review_form.js` sends
`show_pass_result` only when the box is on the page; the words are in
`pass_result_view.py`. A clip whose result was published, corrected or
turned off since it was made shows as out of date with that sentence.

**Running clock** (LIVESTACK-48). For a prova whose configuration has a
clock (`Job.pass_clock.configured`), the job page has a **"Cronômetro"**
block under the result's: the **Largada** mark (like the cuts: "Marcar"
or the <kbd>l</kbd> key at the current frame, rounded *down* to the
millisecond; <kbd>-</kbd>/<kbd>=</kbd> nudge it by a frame once it is the
active mark; an orange line on the cut bar; "Limpar" removes it),
**"Mostrar cronômetro"**, and a line: "Na tarja: da largada até 66,0 s, e
para.", or what is missing (`pass_clock_view.py`). `js/review_form.js`
sends `largada_s` (an empty field as `clear_largada`) and `show_clock`
only when the block is on the page.

**The blocks follow the form** (LIVESTACK-53). The result and clock
blocks sit in two slots (`#pass-result-slot`, `#pass-clock-slot`);
`js/pass_blocks.js` asks `GET /review/api/jobs/{id}/pass-blocks` (which
relays the orchestrator's `GET /jobs/{id}/pass-preview` and renders the
same templates) 300 ms after prova, car or passagem change, and after a
save, and swaps them in place: fields the reviewer set keep their values,
a block that goes away is kept aside (not sent) and comes back with them.
`js/player.js` looks the largada field up when used and listens on the
form, so "Marcar", the <kbd>l</kbd> key and the line on the cut bar work
as soon as the block arrives. With a clock, the clip shows the result
from the clock's freeze on; `js/clock_late.js` shows "O clipe termina
antes do cronômetro parar…" while the cut ends before that (from
`data-official-units` on the clock block and `data-has-text` on the
result block; it never blocks).

**"Pré-visualizar overlay"** (under the player, any job with a proxy):
`js/overlay_preview.js` draws the clip's lower third over the proxy in
HTML/CSS, with the worker's own `tokens.css` and Bebas Neue
(`overlay_theme/`, byte-for-byte copies; `overlay_preview.py` serves the
tokens scoped to `.overlay-preview`, since names like `--radius` mean
something else in `app.css`). The texts come from the server (the names a
render draws, `Job.clip_team_name`/`clip_school_name`; the result line;
the official time in clock units); the form's own fields are read live
(prova, passagem and its toggle, both result/clock toggles, largada,
cuts). The clock runs from the video's `currentTime` on every animation
frame with `js/pass_clock.js`, the same rule as the render (both checked
against `tests/fixtures/pass_clock/vectors.json`), top right with its
unit like the clip, and the result pill waits for its freeze. Outside the
cut nothing is drawn. A quick check, not a pixel copy: names are the saved ones.
**Configurações → "Resultados das passagens"** (`templates/admin/_pass_results.html`,
its own form, `routes_admin_pass_results.py`, `pass_results_settings_view.py`):
the JSON configuration (format in `docs/design.md` §32) with "Salvar" (a
refused text is shown again as typed, with the place and the rule in pt-BR,
e.g. "em `provas.MAN.result.decimals`, o valor deve estar entre 0 e 3") and
"Testar" (tries the text in the box, saved or not, for a prova, car and
passagem: the line or why none, the fetch time, unnamed penalty icons and
the car's row from the results API, shown as text); next to it, each
configured prova's name, table and fetch status.

**Welcome message** (LIVESTACK-44). Configurações has the text the bot
sends to new contacts, its placeholders (`{nome}`, `{prova}`) and a preview
filled with example values (refreshed while typing by `js/welcome.js`,
which mirrors `shared/welcome_message.py`). On Contatos the add form has
"Enviar mensagem de boas-vindas" (ticked for Permitido, disabled for
Bloqueado) and each row a "Boas-vindas" column: when it was last sent and
"Enviar boas-vindas" (asks first). An empty message disables both. When
the orchestrator could not send it, the page answers the POST directly with
a yellow banner and Evolution's reason (`warning` in `admin_context`).

**Review fixes from the 2026-11-07 test** (LIVESTACK-51, design §34).
*Two reviewers*: the page says it is open (`static/js/presence.js`, through
`PUT /review/api/jobs/{id}/presence` every 20 s, `…/presence/leave` on
`pagehide`); opening a video someone else is on shows "Pedro está
revisando este vídeo (desde 14:02)." with "Revisar mesmo assim" and the
form is `inert` until then (it frees itself when the other's presence
ends); words in `presence_view.py`. *Duplicates*: a copy says "Descartado
automaticamente: duplicado de <first job>", the first job "Também recebido
de: Maria, 14:40" (status strip); Configurações holds the bot's reply.
*Girar*: the page's own control bar under the picture (play, frame step,
time, the cut bar as scrubber) never turns; the geometry is
`playerLayout`/`cropBandBox` in `js/render_options.js`. *Contatos*: a row
says how many of the sender's videos wait without a prova, and saving asks
whether to apply the default prova to them (`js/admin.js`).

Every action sends the version of the job the page was loaded with
(`expected_version`). If someone else changed the job first, the
orchestrator answers 409. The page then reloads and says so, listing the
values the reviewer had typed.

## Language (pt-BR, LIVESTACK-39)

Every text a person reads is pt-BR and lives in **one catalog**, the
`catalog/` package (split into modules by area only to keep files short;
a key may be defined once). Keys are English (`queue.title`,
`state.awaiting_review`, `error.not_found`); no sentence is written inline
in a template, a view or a script.

* Views call `text(key, **values)`; templates call `t(key, ...)` and
  `tn(key, count)` (plurals: `<key>.one` / `<key>.other`). Entries may hold
  a little HTML (`<kbd>`, `<code>`, links); the values filled in are escaped.
* The scripts get the `script.*` entries as JSON in every page
  (`<script id="ui-text">` in `base.html`), read by `static/js/text.js`.
  Same `{name}` placeholders on both sides.
* API values stay English (job states, actions, error codes, event kinds in
  the DB); only their labels are translated. Text from the orchestrator or a
  worker (failure messages, event reasons, refusal details) is shown as it
  came, inside `lang="en"`. The waiting reasons the orchestrator writes
  itself are recognised and shown in pt-BR.
* Dates: `05/11/2026 09:23:41`, 24-hour clock, America/Sao_Paulo, whatever
  the laptop's zone; relative ones read `há 5 min (09:18)`. Numbers shown to
  people use a decimal comma (`1,5 GB`, `29,97 fps`, `1:02,500`); form input
  values keep the point HTML needs. `formatting.py` and `static/js/format.js`
  are tested with the same cases (`tests/review_ui/test_formats.py`, Node).
* Guards: `test_pt_br_guard.py` renders every page and partial and fails on
  common English UI words (allowlist for WhatsApp, proxy, HDR, codec names…);
  it also checks the scripts' string literals. `test_catalog.py` checks that
  every state, action, task, error code and event kind has a text.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `BVP_ORCHESTRATOR_URL` | `http://127.0.0.1:8000` | Orchestrator API (`http://orchestrator:8000` in compose) |
| `BVP_PUBLIC_BASE_URL` | empty | Public site; proxy/thumbnail paths are made absolute on it. Empty = same site as the UI |
| `BVP_REVIEW_UI_PORT` | `8001` | Listening port |
| `BVP_ORCHESTRATOR_TIMEOUT_S` | `10` | Per-call timeout; slower calls show a 502 page |
| `BVP_STUDIO_CLIP_ROOT` | empty | Optional clip folder on the studio box (e.g. `D:\Baja\clips`), so the clips page copies full paths |

## Run locally

```sh
uv run python tests/review_ui/dev_server.py        # fake orchestrator, http://127.0.0.1:8001/review/ (ana / secret)
uv run python -m review_ui                         # against a real orchestrator at BVP_ORCHESTRATOR_URL
uv run pytest tests/review_ui
```

`dev_server.py --media-dir .scratch/media` also serves
`proxies/<job_id>.mp4` and `thumbs/<job_id>.jpg` from that folder, the way
Caddy serves them on the VPS.

## Image

```sh
docker build -f review_ui/Dockerfile -t ghcr.io/jbresolin/baja-review-ui:<tag> .   # from the repo root
```

The image is `python:3.12-slim` with uv, runs as non-root user 10001, listens
on 8001, and has a health check on `/healthz`. In `infra/vps/docker-compose.yml`
it is the `review-ui` service (image `$REVIEW_UI_IMAGE`), reached by the
base Caddy as `bvp-review-ui:8001` on `baja-edge`.

## Code map

| File | What |
| --- | --- |
| `app.py` | `create_app()`: wiring, static files, routers |
| `orchestrator_client.py` | Every orchestrator call, with the reviewer's Authorization |
| `routes_queue.py`, `routes_job.py`, `routes_clips_fleet.py` | HTML pages and their refresh fragments |
| `routes_api.py` | JSON endpoints for the page scripts (review actions, proxy request, team lookup, the passagem panel) |
| `routes_admin_pass_results.py` | Configurações → "Resultados das passagens": "Salvar" and "Testar" (LIVESTACK-47) |
| `routes_admin_inline.py` | JSON endpoints behind the admin toggles that change a row in place (provas active/order, contacts allow/block): they answer with the re-rendered rows (`templates/admin/_provas_rows.html`, `_contact_row.html`) and `static/js/admin.js` swaps them in, so the page neither reloads nor scrolls |
| `errors.py` | Error page for browsers, JSON errors for scripts, 401 sign-in challenge |
| `job_view.py`, `queue_view.py`, `clips_and_fleet_view.py` | Plain functions that turn API data into what the pages show |
| `passagem_help_view.py` | The job page's passagem panel: the car's videos in filming order, the suggestion and the warnings (LIVESTACK-45) |
| `sender_messages_view.py` | The job page's "Mensagens de <remetente>": title, open/folded lines (LIVESTACK-49) |
| `clip_inputs_view.py` | A clip that no longer matches its review, in sentences (job page banner, clips page detail) |
| `pass_result_view.py`, `pass_results_settings_view.py` | The official result of the passagem: the job page's line, the Settings block (status, refusals, "Testar") (LIVESTACK-47) |
| `pass_clock_view.py`, `overlay_preview.py` | The running clock's block and the overlay preview's data; the scoped overlay theme (LIVESTACK-48); `GET …/pass-blocks` for unsaved values is in `routes_api.py` (LIVESTACK-53) |
| `render_options_view.py` | The job page's "Imagem e som" box: orientation sentence, whether "Enquadramento" applies, the choices (LIVESTACK-50) |
| `event_log_view.py` | Orchestrator events (`Event`) in words: actor names, state changes, reasons; used by the job and fleet pages |
| `catalog/` | Every pt-BR text the UI shows, by English key (see "Language") |
| `formatting.py` | Dates (São Paulo time) and numbers (decimal comma) for the templates |
| `templates/`, `static/` | Jinja2 templates; CSS and small ES modules (no build step); `js/text.js` and `js/format.js` mirror the two above |
