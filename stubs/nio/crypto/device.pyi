class OlmDevice:
    def __init__(
        self,
        user_id: str,
        device_id: str,
        keys: dict[str, str],
        display_name: str = ...,
        deleted: bool = ...,
    ) -> None: ...
