"""Installed launcher for the compact glass quota monitor."""
if __name__ == '__main__':
    try:
        from glass_overlay import main
        raise SystemExit(main())
    except Exception:
        import traceback
        from monitor import state_directory
        directory = state_directory()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'ui-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise
