from config import Settings, load_settings


def test_settings_ignore_undefined_environment_variables(monkeypatch):
    monkeypatch.setenv("APP_NAME", "capstone-test")

    settings = Settings()

    assert settings.model_extra is None


def test_load_settings_caches_instance():
    load_settings.cache_clear()

    first_settings = load_settings()
    second_settings = load_settings()

    assert first_settings is second_settings

    load_settings.cache_clear()
