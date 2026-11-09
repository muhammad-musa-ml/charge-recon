# Results

Synthetic data only. 20 seeds, 6657 charge keys in all (287 to 372 per seed), 779 of them with an injected fault (29 to 55 per seed).

| approach | keys matching their label exactly | missed | spurious | wrong kinds |
|---|---|---|---|---|
| this pipeline | 6657 of 6657 | 0 | 0 | 0 |
| add up every row of every file, then the same ACH replay | 4130 of 6657 | 0 | 2334 | 193 |
| this ingest, then count every settled event as collected | 6164 of 6657 | 124 | 360 | 0 |

Per seed, the plain row-adding approach matched 35.2% to 78.2% of keys and the settled-event count 90.1% to 94.1%.

Side channels over all seeds: ACH violations seen 78 of 78 expected; quarantined files seen 15 of 15 expected; pending payments matched in 20 of 20 seeds; store and report identical under a second arrival order in 20 of 20 seeds.

## Scenarios injected, all seeds

| scenario | count |
|---|---|
| clean | 5473 |
| drop_posting | 138 |
| in_flight | 47 |
| late_r10 | 127 |
| r01_exhausted | 123 |
| r01_retry_ok | 280 |
| r02_final | 123 |
| resend_corrected | 344 |
| return_after_window | 78 |
| unexpected_posting | 54 |
| wrong_down | 101 |
| wrong_up | 113 |

## Per seed

| seed | keys | faults | exact | missed | spurious | ACH viol. | quarantined | order stable | row-adding exact | settled-count exact |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 331 | 39 | 331 | 0 | 0 | 4/4 | 1/1 | yes | 259 | 301 |
| 2 | 287 | 30 | 287 | 0 | 0 | 6/6 | 1/1 | yes | 169 | 270 |
| 3 | 348 | 55 | 348 | 0 | 0 | 4/4 | 0/0 | yes | 227 | 318 |
| 4 | 361 | 39 | 361 | 0 | 0 | 3/3 | 0/0 | yes | 262 | 330 |
| 5 | 353 | 44 | 353 | 0 | 0 | 2/2 | 0/0 | yes | 265 | 332 |
| 6 | 310 | 39 | 310 | 0 | 0 | 2/2 | 1/1 | yes | 215 | 286 |
| 7 | 372 | 43 | 372 | 0 | 0 | 3/3 | 1/1 | yes | 236 | 343 |
| 8 | 342 | 37 | 342 | 0 | 0 | 4/4 | 1/1 | yes | 149 | 308 |
| 9 | 358 | 50 | 358 | 0 | 0 | 5/5 | 1/1 | yes | 126 | 332 |
| 10 | 327 | 37 | 327 | 0 | 0 | 5/5 | 0/0 | yes | 242 | 302 |
| 11 | 348 | 33 | 348 | 0 | 0 | 3/3 | 1/1 | yes | 225 | 327 |
| 12 | 337 | 38 | 337 | 0 | 0 | 3/3 | 0/0 | yes | 250 | 315 |
| 13 | 311 | 34 | 311 | 0 | 0 | 6/6 | 1/1 | yes | 162 | 287 |
| 14 | 337 | 39 | 337 | 0 | 0 | 7/7 | 1/1 | yes | 257 | 317 |
| 15 | 358 | 47 | 358 | 0 | 0 | 2/2 | 1/1 | yes | 223 | 334 |
| 16 | 304 | 42 | 304 | 0 | 0 | 8/8 | 1/1 | yes | 200 | 285 |
| 17 | 336 | 37 | 336 | 0 | 0 | 1/1 | 1/1 | yes | 150 | 312 |
| 18 | 324 | 36 | 324 | 0 | 0 | 4/4 | 1/1 | yes | 179 | 299 |
| 19 | 305 | 29 | 305 | 0 | 0 | 4/4 | 1/1 | yes | 164 | 281 |
| 20 | 308 | 31 | 308 | 0 | 0 | 2/2 | 1/1 | yes | 170 | 285 |
