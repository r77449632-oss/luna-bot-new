"""FSM holatini SQLite bazada saqlaydi: bot qayta ishga tushsa ham ariza va shikoyat yo'qolmaydi."""
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

import db


def _k(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}"


class DbStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state=None) -> None:
        name = state.state if isinstance(state, State) else state
        db.fsm_set_state(_k(key), name)

    async def get_state(self, key: StorageKey):
        state, _ = db.fsm_get(_k(key))
        return state

    async def set_data(self, key: StorageKey, data: dict) -> None:
        db.fsm_set_data(_k(key), data)

    async def get_data(self, key: StorageKey) -> dict:
        _, data = db.fsm_get(_k(key))
        return data

    async def close(self) -> None:
        pass
