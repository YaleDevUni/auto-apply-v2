"""Temporal 공통 설정.

DTO 가 pydantic BaseModel 이므로 pydantic data converter 가 필요하다.
client·worker·테스트가 모두 여기서 같은 converter 를 가져와야 payload 가 호환된다.
"""

from temporalio.contrib.pydantic import pydantic_data_converter

DATA_CONVERTER = pydantic_data_converter

QUEUE_DEFAULT = "default"
QUEUE_AI = "ai"
