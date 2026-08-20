-- postgres 컨테이너 최초 기동(빈 볼륨) 시 1회 실행된다 (docker-entrypoint-initdb.d 관례).
-- tests/ports/test_repository_contract.py 의 postgres 파라미터가 매 테스트 전에
-- TRUNCATE 하는 대상을 운영 DATABASE_URL(auto_apply)과 분리하기 위한 전용 스키마.
-- 이미 떠 있는 기존 볼륨에는 적용되지 않으니 그 경우 수동으로 한 번 만든다:
--   docker exec auto-apply-postgres-1 psql -U auto_apply -d auto_apply \
--     -c "CREATE DATABASE auto_apply_test OWNER auto_apply;"
CREATE DATABASE auto_apply_test OWNER auto_apply;
