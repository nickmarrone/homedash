"""What every router needs, in one place.

`settings` is a module attribute here rather than a module-level global in each
router. That is the whole reason this module exists: the routers were split up
and a per-module copy would have turned one config seam into five, so a test
wanting a different home timezone would have to know which router file the
endpoint it is testing happens to live in.

Reference it as `deps.settings`, never `from app.api.deps import settings` - the
second binds a copy and a test patching this module would not be seen.
"""

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session

settings = get_settings()

SessionDep = Annotated[Session, Depends(get_session)]
