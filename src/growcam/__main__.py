"""Allow ``python -m growcam`` to invoke the command-line interface."""

from .cli import main

raise SystemExit(main())
