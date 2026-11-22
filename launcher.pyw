# Shim de compatibilidade (Windows, sem console): toda a lógica está em launcher.py.
from launcher import main
raise SystemExit(main())
