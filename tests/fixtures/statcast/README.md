# Statcast fixtures

`statcast_2025-09-01.csv` is a trimmed one-day Statcast Search CSV: 6 pitches from two games,
with the full 118-column header of the 2025 export (including the deprecated columns staging
drops). `tests/test_statcast.py` parses it with pybaseball's own CSV parser
(`get_statcast_data_from_csv`), so the DataFrame has the same dtypes `pybaseball.statcast()`
returns.

**Provenance:** the header follows the Savant CSV docs, but the rows are hand-written (plausible
values, made-up `game_pk`s), because the environment that built M1 couldn't reach
baseballsavant.mlb.com. To replace it with a real recording, run on a machine with access:

```bash
docker compose run --rm pipeline python -c "
import pybaseball
df = pybaseball.statcast('2025-09-01', '2025-09-01', verbose=False, parallel=False)
df[df.game_pk.isin(df.game_pk.unique()[:2])].groupby('game_pk').head(3) \
  .to_csv('tests/fixtures/statcast/statcast_2025-09-01.csv', index=False)"
```

Then update `FIXTURE_ROWS` in `tests/test_statcast.py` if the row count changes.
