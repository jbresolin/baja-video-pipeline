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
