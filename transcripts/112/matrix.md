# Finding 112 matrix

UTC window: 2026-10-31T05:56:13Z to 2026-10-31T05:59:27Z

| Cell | Served by | Runs | Result |
|---|---|---|---|
| consumer-createai-agent | first step external | 5 | 5/5 had invalid calls; 2/5 ended with an empty answer at the step limit; 1/5 never ran both tools; steps used [3, 3, 4, 5, 5] |
| consumer-createai-agent | first step native | 1 | 0/1 had invalid calls; 0/1 ended with an empty answer; steps used [2] |
| direct-openrouter-parallel-nonstream | PrimeIntellect | 5 | broken 0/5, ok 5/5 |
| direct-openrouter-parallel-stream | PrimeIntellect | 5 | broken 0/5, ok 5/5 |
| plain-parallel-nonstream | external | 9 | broken 9/9, ok 0/9 |
| plain-parallel-nonstream | native | 1 | broken 0/1, ok 1/1 |
| plain-parallel-stream | external | 8 | broken 8/8, ok 0/8 |
| plain-parallel-stream | native | 2 | broken 0/2, ok 2/2 |
| trigger-removed-single-nonstream | external | 5 | broken 0/5, ok 5/5 |
| trigger-removed-single-stream | external | 4 | broken 0/4, ok 4/4 |
| trigger-removed-single-stream | native | 1 | broken 0/1, ok 1/1 |
