import sys, types
from app.transcription import audio_transcriber as at

def test_compatibiliza_pyav_sem_metadata_errors(monkeypatch):
    av = types.ModuleType("av")
    def abrir(file, mode="r", **kw):
        if "metadata_errors" in kw:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        return ("aberto", file)
    av.open = abrir
    monkeypatch.setitem(sys.modules, "av", av)
    at._compatibilizar_pyav()
    at._compatibilizar_pyav()  # idempotente
    assert av.open("x.mp4", mode="r", metadata_errors="ignore") == ("aberto", "x.mp4")
