# catalog-data

Text lists generated from public domain and address datasets.

Example configuration: [default.conf](default.conf). Use Config mode and enable automatic list updates in the client. Keep local overrides above the downloaded lists.

- `domains.list`: selected domains.
- `addresses.list`: selected address ranges.
- `exceptions.list`: domain exceptions; apply these before the selected lists.
- `excluded.list`: excluded domains; apply these first.

The [update workflow](.github/workflows/update.yml) runs daily and can be started manually. Invalid inputs stop the update and leave the published files intact. Each file records its source revisions and build date.

To build locally, use Python 3.13 or later:

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m unittest discover -s tests
python scripts/update.py
```

Source selection and conversion are defined in [scripts/update.py](scripts/update.py). Attribution is in [NOTICE](NOTICE).
