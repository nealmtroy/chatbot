import os

def get_session_path(session_name: str) -> str:
    """
    Memastikan seluruh file Telethon session (.session & .session-journal)
    tersimpan rapi di dalam sub-folder 'sessions/' daripada memenuhi root directory.
    Secara otomatis memindahkan (.session) lama dari root ke 'sessions/' jika ada.
    """
    if not session_name:
        session_name = "default"

    session_dir = "sessions"
    os.makedirs(session_dir, exist_ok=True)

    # Normalize name if path or .session extension is provided
    basename = os.path.basename(session_name)
    if basename.endswith(".session"):
        clean_name = basename[:-8]
    else:
        clean_name = basename

    target_path = os.path.join(session_dir, clean_name)

    # Auto-migrate root .session if exists
    root_session = clean_name + ".session"
    target_session = target_path + ".session"

    if os.path.exists(root_session) and not os.path.exists(target_session):
        try:
            os.rename(root_session, target_session)
            if os.path.exists(root_session + "-journal"):
                os.rename(root_session + "-journal", target_session + "-journal")
        except Exception:
            pass

    return target_path
