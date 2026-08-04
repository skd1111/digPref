"""test_codenav_watcher.py —— 文件监听 → 增量索引测试。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from agent.codenav.indexer import WorkspaceIndexer
from agent.codenav.query import SymbolQuery
from agent.codenav.watcher import FileWatcher


@pytest.mark.asyncio
async def test_incremental_update_new_file(tmp_path):
    (tmp_path / "old.py").write_text("def a(): pass", encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    await idx.full_scan()

    new_file = tmp_path / "new.py"
    new_file.write_text("def b(): pass", encoding="utf-8")
    status = await idx.incremental_update([str(new_file)])
    assert status.last_incremental is not None

    q = SymbolQuery(str(tmp_path / "idx.db"))
    found = q.search("b")
    assert len(found) == 1


@pytest.mark.asyncio
async def test_incremental_update_modified_file(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("def old(): pass", encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    await idx.full_scan()

    # 修改文件：增加一个新符号
    f.write_text("def old(): pass\ndef added(): pass", encoding="utf-8")
    # mtime 强制更新
    time.sleep(0.05)
    new_mtime = time.time() + 1
    import os
    os.utime(str(f), (new_mtime, new_mtime))

    await idx.incremental_update([str(f)])
    q = SymbolQuery(str(tmp_path / "idx.db"))
    assert any(s.name == "added" for s in q.search("added"))


@pytest.mark.asyncio
async def test_incremental_update_deleted_file(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("def gone(): pass", encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    await idx.full_scan()

    f.unlink()
    await idx.incremental_update([str(f)])
    q = SymbolQuery(str(tmp_path / "idx.db"))
    assert q.search("gone") == []


@pytest.mark.asyncio
async def test_watcher_start_stop(tmp_path):
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    watcher = FileWatcher(idx, [str(tmp_path)])
    await watcher.start()
    assert watcher._task is not None
    await watcher.stop()
    # stop 后 task 应已完成
    assert watcher._task is None or watcher._task.done()


def test_watcher_should_skip_ignored_dirs(tmp_path):
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    watcher = FileWatcher(idx, [str(tmp_path)])
    assert watcher._should_skip(str(tmp_path / "node_modules" / "foo.js"))
    assert watcher._should_skip(str(tmp_path / ".git" / "HEAD"))
    assert watcher._should_skip(str(tmp_path / "__pycache__" / "x.pyc"))
    assert not watcher._should_skip(str(tmp_path / "src" / "main.py"))
