"""Small DB-API adapter for the official SQLite library bundled with FTS5.

All Android repositories use this connection; databases retain SQLite's format.
All SQL and transaction rules remain in the existing repository.
"""
import ctypes as C
import os
import sqlite3

_lib = None

def _library():
    global _lib
    if _lib is not None: return _lib
    lib = C.CDLL(os.path.join(os.environ['BINGDU_NATIVE_LIB_DIR'], 'libbingdusqlite.so'))
    signatures = {
        'open_v2': ([C.c_char_p, C.POINTER(C.c_void_p), C.c_int, C.c_char_p], C.c_int),
        'close_v2': ([C.c_void_p], C.c_int), 'errmsg': ([C.c_void_p], C.c_char_p),
        'busy_timeout': ([C.c_void_p, C.c_int], C.c_int),
        'prepare_v2': ([C.c_void_p, C.c_char_p, C.c_int, C.POINTER(C.c_void_p), C.c_void_p], C.c_int),
        'bind_null': ([C.c_void_p, C.c_int], C.c_int),
        'bind_int64': ([C.c_void_p, C.c_int, C.c_longlong], C.c_int),
        'bind_double': ([C.c_void_p, C.c_int, C.c_double], C.c_int),
        'bind_text': ([C.c_void_p, C.c_int, C.c_char_p, C.c_int, C.c_void_p], C.c_int),
        'bind_blob': ([C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_void_p], C.c_int),
        'step': ([C.c_void_p], C.c_int), 'finalize': ([C.c_void_p], C.c_int),
        'column_count': ([C.c_void_p], C.c_int), 'column_name': ([C.c_void_p, C.c_int], C.c_char_p),
        'column_type': ([C.c_void_p, C.c_int], C.c_int),
        'column_int64': ([C.c_void_p, C.c_int], C.c_longlong),
        'column_double': ([C.c_void_p, C.c_int], C.c_double),
        'column_text': ([C.c_void_p, C.c_int], C.c_void_p),
        'column_blob': ([C.c_void_p, C.c_int], C.c_void_p),
        'column_bytes': ([C.c_void_p, C.c_int], C.c_int),
        'changes': ([C.c_void_p], C.c_int), 'last_insert_rowid': ([C.c_void_p], C.c_longlong),
        'get_autocommit': ([C.c_void_p], C.c_int),
        'exec': ([C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p], C.c_int),
        'backup_init': ([C.c_void_p, C.c_char_p, C.c_void_p, C.c_char_p], C.c_void_p),
        'backup_step': ([C.c_void_p, C.c_int], C.c_int),
        'backup_finish': ([C.c_void_p], C.c_int),
    }
    for name, (arguments, result) in signatures.items():
        fn = getattr(lib, 'sqlite3_' + name); fn.argtypes = arguments; fn.restype = result
    _lib = lib
    return lib

class Row(dict):
    def __init__(self, names, values):
        # sqlite3.Row resolves duplicate column names to the first column.
        super().__init__()
        for name, value in zip(names, values): self.setdefault(name, value)
        self.values_in_order = values
    def __getitem__(self, key):
        return self.values_in_order[key] if isinstance(key, (int, slice)) else super().__getitem__(key)

class Cursor:
    def __init__(self, connection):
        self.connection = connection; self.stmt = C.c_void_p(); self.rowcount = -1
        self.lastrowid = None; self.description = None; self.current = None
    def execute(self, sql, parameters=()):
        self.close(); db = self.connection; lib = db.lib
        if sql.lstrip().split(None, 1)[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'} and not db.in_transaction:
            db._exec('BEGIN')
        db._check(lib.sqlite3_prepare_v2(db.handle, sql.encode(), -1, C.byref(self.stmt), None))
        if not self.stmt.value: return self
        try:
            for index, value in enumerate(parameters, 1):
                if value is None: result = lib.sqlite3_bind_null(self.stmt, index)
                elif isinstance(value, int): result = lib.sqlite3_bind_int64(self.stmt, index, value)
                elif isinstance(value, float): result = lib.sqlite3_bind_double(self.stmt, index, value)
                elif isinstance(value, (bytes, bytearray)):
                    raw = bytes(value); result = lib.sqlite3_bind_blob(self.stmt, index, raw, len(raw), C.c_void_p(-1))
                else:
                    raw = str(value).encode(); result = lib.sqlite3_bind_text(self.stmt, index, raw, len(raw), C.c_void_p(-1))
                db._check(result)
            count = lib.sqlite3_column_count(self.stmt)
            names = [lib.sqlite3_column_name(self.stmt, i).decode() for i in range(count)]
            self.description = [(name, None, None, None, None, None, None) for name in names] if count else None
            self._step()
            self.rowcount = -1 if count else lib.sqlite3_changes(db.handle)
            self.lastrowid = lib.sqlite3_last_insert_rowid(db.handle)
            return self
        except Exception:
            self.close(); raise
    def _step(self):
        lib = self.connection.lib; result = lib.sqlite3_step(self.stmt)
        if result == 101: self.current = None; return
        self.connection._check(result, allow_row=True)
        values = []
        for i in range(lib.sqlite3_column_count(self.stmt)):
            kind = lib.sqlite3_column_type(self.stmt, i)
            if kind == 1: value = lib.sqlite3_column_int64(self.stmt, i)
            elif kind == 2: value = lib.sqlite3_column_double(self.stmt, i)
            elif kind == 5: value = None
            else:
                ptr = lib.sqlite3_column_blob(self.stmt, i) if kind == 4 else lib.sqlite3_column_text(self.stmt, i)
                value = C.string_at(ptr, lib.sqlite3_column_bytes(self.stmt, i)) if ptr else b''
                if kind == 3: value = value.decode()
            values.append(value)
        self.current = Row([item[0] for item in self.description], values) if self.connection.row_factory else tuple(values)
    def fetchone(self):
        result = self.current
        if result is not None: self._step()
        return result
    def fetchall(self): return list(self)
    def __iter__(self):
        while self.current is not None: yield self.fetchone()
    def executemany(self, sql, values):
        count = 0
        for parameters in values:
            self.execute(sql, parameters); count += max(0, self.rowcount)
        self.rowcount = count; return self
    def close(self):
        if self.stmt.value: self.connection.lib.sqlite3_finalize(self.stmt); self.stmt = C.c_void_p()
        self.current = None
    def __del__(self): self.close()

class Connection:
    def __init__(self, path, timeout=15, check_same_thread=True):
        self.lib = _library(); self.handle = C.c_void_p(); self.row_factory = None
        self._check(self.lib.sqlite3_open_v2(str(path).encode(), C.byref(self.handle), 0x2 | 0x4 | 0x10000, None))
        self.lib.sqlite3_busy_timeout(self.handle, int(timeout * 1000))
    def _check(self, result, allow_row=False):
        if result == 0 or allow_row and result == 100: return
        message = self.lib.sqlite3_errmsg(self.handle).decode()
        error = sqlite3.IntegrityError if result & 255 == 19 else sqlite3.OperationalError
        raise error(message)
    @property
    def in_transaction(self): return not bool(self.lib.sqlite3_get_autocommit(self.handle))
    def _exec(self, sql): self._check(self.lib.sqlite3_exec(self.handle, sql.encode(), None, None, None))
    def execute(self, sql, parameters=()): return Cursor(self).execute(sql, parameters)
    def executemany(self, sql, values): return Cursor(self).executemany(sql, values)
    def executescript(self, sql): self.commit(); self._exec(sql)
    def commit(self):
        if self.in_transaction: self._exec('COMMIT')
    def rollback(self):
        if self.in_transaction: self._exec('ROLLBACK')
    def close(self):
        if self.handle.value: self.lib.sqlite3_close_v2(self.handle); self.handle = C.c_void_p()
    def __del__(self):
        if hasattr(self, 'handle'): self.close()
    def backup(self, target):
        self.commit()
        handle = self.lib.sqlite3_backup_init(target.handle, b'main', self.handle, b'main')
        if not handle: target._check(1)
        try:
            result = self.lib.sqlite3_backup_step(handle, -1)
            if result != 101: target._check(result)
        finally: target._check(self.lib.sqlite3_backup_finish(handle))
    def create_function(self, name, arguments, function, **kwargs):
        if name != 'pow' or arguments != 2: raise NotImplementedError(name)
        # pow is compiled into SQLite with SQLITE_ENABLE_MATH_FUNCTIONS.
    def __enter__(self): return self
    def __exit__(self, kind, value, traceback):
        self.rollback() if kind else self.commit()

connect = Connection
