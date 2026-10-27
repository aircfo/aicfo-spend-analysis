# Lets cloud routines run the tool straight from a checkout, with no install:
# PYTHONPATH=src python3 -m aicfo_spend ...
from aicfo_spend.cli import main

raise SystemExit(main())
