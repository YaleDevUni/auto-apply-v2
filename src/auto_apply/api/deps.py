"""FastAPI 의존성. 라우터는 어댑터를 직접 만들지 않고 컨테이너에서 꺼내 쓴다 (§11.4)."""

from typing import Annotated, cast

from fastapi import Depends, Request

from auto_apply.bootstrap import Container


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]
