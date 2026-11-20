# Results API samples

Real answers of the Baja results API (public data, `docs/results-api.md` of
tiagomartines11/baja-sae-brasil-online), for tests that read pass data.

* `26BR_MAN.json`: `/resultados/resultado.php?evt=26BR&id=26BR_MAN`
  (Manobrabilidade, 2026 national event): two runs per car in the list
  columns "Tempo" and "Tempo Corrigido"; "-" for a run not done.
* `26BR_MAN.display.json`: the same with `&display=1`. The display HTML
  carries the penalties: `66 (4<img class='cone'>)` = 4 cones,
  `58 (8<img class='cone'>/2<img class='gate'>)` = 8 cones and 2 gates.
  Runs are separated by `<br />`; the best one is in `<b>`.

Do not edit them by hand: replace them with a fresh answer from the API.

## Aceleração e Velocidade (LIVESTACK-67)

* `26BR_ACE.display.json` (column "Aceleração", seconds) and
  `26BR_VEL.display.json` (column "Velocidade", km/h): the two results
  tables of prova AVF, in the API's `&display=1` shape. **Derived, not
  fetched**: the API's answers for these tables were not captured, so they
  were built from the old results-page snapshots of the scoring repository
  (tiagomartines11/baja-sae-brasil-online,
  `baja-php/tests/results-snapshots/26BR/26BR_ACE.json`, `26BR_VEL.json`
  and `formulas.AVF.json`, where run n of ACE pairs with run n of VEL).
* Kept from the snapshots: cars 1, 2, 4 and 7, their positions, points
  and every run value as published ("4.354<br> <b>4.328</b>" became a
  list cell, raw `[4.354, 4.328]`, display `4.354<br /> <b>4.328</b>`; a
  run not done stays `"-"`). Car 2 did one run only: its second run has
  no acceleration and no speed.
* Changed: the team names, schools and states are **invented** (this
  repository is public), and the old pages' `<br>` is written `<br />` as
  the API writes it.

These two may be edited by rebuilding them from the snapshots; replace
them with real API answers once they can be captured.
