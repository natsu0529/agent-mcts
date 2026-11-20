## Per run

| task | arm | rep | episodes | first attempt | chosen | best in tree | mean of attempts | min |
|---|---|---|---|---|---|---|---|---|
| cron | judge | 1 | 5 | 99% | 99% (n5) | 100% | 84% | 19.1 |
| cron | judge | 2 | 5 | 100% | 100% (n5) | 100% | 86% | 18.8 |
| cron | tests | 1 | 2 | 35% | 100% (n2) | 100% | 68% | 5.2 |
| cron | tests | 2 | 1 | 100% | 100% (n1) | 100% | 100% | 1.9 |
| deriv | judge | 1 | 5 | 98% | 100% (n3) | 100% | 99% | 20.0 |
| deriv | judge | 2 | 5 | 98% | 98% (n1) | 98% | 98% | 15.2 |
| deriv | tests | 1 | 1 | 98% | 98% (n1) | 98% | 98% | 6.2 |
| deriv | tests | 2 | 1 | 71% | 71% (n1) | 71% | 71% | 1.6 |
| glob | judge | 1 | 5 | 100% | 100% (n1) | 100% | 97% | 27.4 |
| glob | judge | 2 | 5 | 100% | 98% (n4) | 100% | 96% | 26.4 |
| glob | tests | 1 | 1 | 99% | 99% (n1) | 99% | 99% | 3.5 |
| glob | tests | 2 | 1 | 87% | 87% (n1) | 87% | 87% | 0.8 |

## Per task and arm (mean over repeats)

| task | arm | runs | episodes | first attempt | chosen | best in tree | min |
|---|---|---|---|---|---|---|---|
| cron | judge | 2 | 5.0 | 99% | 99% | 100% | 18.9 |
| cron | tests | 2 | 1.5 | 68% | 100% | 100% | 3.5 |
| deriv | judge | 2 | 5.0 | 98% | 99% | 99% | 17.6 |
| deriv | tests | 2 | 1.0 | 84% | 84% | 84% | 3.9 |
| glob | judge | 2 | 5.0 | 100% | 99% | 100% | 26.9 |
| glob | tests | 2 | 1.0 | 93% | 93% | 93% | 2.1 |
| ALL | judge | 6 | 5.0 | 99% | 99% | 100% | 21.2 |
| ALL | tests | 6 | 1.2 | 82% | 93% | 93% | 3.2 |

## Does the reward rank attempts the way the hidden tests do?

Pairs of attempts in the same run whose hidden scores differ: how often the
higher reward went to the better one.

| arm | pairs | reward agrees | reward ties | reward disagrees |
|---|---|---|---|---|
| judge | 42 | 26 | 1 | 15 |
| tests | 1 | 1 | 0 | 0 |

## Revisions: did a child score higher than its parent on the hidden tests?

| arm | revisions | better | same | worse | mean change |
|---|---|---|---|---|---|
| judge | 12 | 2 | 9 | 1 | -0.4 pt |
| tests | 0 | - | - | - | - |
