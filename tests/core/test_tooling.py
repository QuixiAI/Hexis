import pytest

from core.cognitive_memory_api import CognitiveMemory, MemoryType
from services.tooling import execute_tool
from tests.utils import _db_dsn, get_test_identifier

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


@pytest.fixture(scope="module")
async def mem_client(ensure_embedding_service):
    client = await CognitiveMemory.create(_db_dsn(), min_size=1, max_size=5)
    yield client
    await client.close()


async def test_execute_tool_unknown(mem_client):
    result = await execute_tool("nope", {}, mem_client=mem_client)
    assert "error" in result


async def test_tool_recall(mem_client, db_pool):
    test_id = get_test_identifier("tool_recall")
    content = f"Tool recall {test_id}"
    mid = await mem_client.remember(content, type=MemoryType.SEMANTIC, importance=0.7)
    try:
        result = await execute_tool("recall", {"query": content, "limit": 5}, mem_client=mem_client)
        assert result["count"] >= 1
        assert any(m["memory_id"] == str(mid) for m in result["memories"])
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", mid)


async def test_tool_recall_recent(mem_client, db_pool):
    test_id = get_test_identifier("tool_recent")
    content = f"Recent {test_id}"
    mid = await mem_client.remember(content, type=MemoryType.SEMANTIC, importance=0.6)
    try:
        result = await execute_tool("recall_recent", {"limit": 5}, mem_client=mem_client)
        assert result["count"] >= 1
        assert any(m["memory_id"] == str(mid) for m in result["memories"])
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", mid)


async def test_tool_recall_episode(mem_client, db_pool):
    test_id = get_test_identifier("tool_episode")
    content = f"Episode {test_id}"
    mid = await mem_client.remember(content, type=MemoryType.EPISODIC, importance=0.6)
    try:
        async with db_pool.acquire() as conn:
            episode_id = await conn.fetchval(
                """
                SELECT e.id
                FROM episodes e
                CROSS JOIN LATERAL find_episode_memories_graph(e.id) fem
                WHERE fem.memory_id = $1
                ORDER BY e.started_at DESC
                LIMIT 1
                """,
                mid,
            )
        result = await execute_tool("recall_episode", {"episode_id": str(episode_id)}, mem_client=mem_client)
        assert result["count"] >= 1
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", mid)


async def test_tool_explore_concept(mem_client, db_pool):
    """Test explore_concept tool finds memories linked to a concept.
    Phase 2 (ReduceScopeCreep): Concepts are now graph-only.
    """
    test_id = get_test_identifier("tool_concept")
    content = f"Concept memory {test_id}"
    concept = f"Concept_{test_id}"
    mid = await mem_client.remember(content, type=MemoryType.SEMANTIC, concepts=[concept], importance=0.6)
    try:
        result = await execute_tool("explore_concept", {"concept": concept}, mem_client=mem_client)
        assert result["count"] >= 1
        assert any(m["memory_id"] == str(mid) for m in result["memories"])
    finally:
        # Note: concepts table removed in Phase 2 - concepts are now graph-only
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", mid)


async def test_tool_explore_cluster(mem_client, db_pool):
    test_id = get_test_identifier("tool_cluster")
    content = f"Cluster memory {test_id}"
    memory_id = await mem_client.remember(content, type=MemoryType.SEMANTIC, importance=0.6)
    async with db_pool.acquire() as conn:
        cluster_id = await conn.fetchval(
            """
            INSERT INTO clusters (cluster_type, name, centroid_embedding)
            VALUES ('theme', $1, array_fill(0.1, ARRAY[embedding_dimension()])::vector)
            RETURNING id
            """,
            f"Cluster {test_id}",
        )
        # Phase 3 (ReduceScopeCreep): Use graph edges instead of memory_cluster_members
        await conn.execute("SELECT sync_memory_node($1)", memory_id)
        await conn.execute(
            "SELECT link_memory_to_cluster_graph($1, $2, $3)",
            memory_id, cluster_id, 0.9
        )
    try:
        result = await execute_tool("explore_cluster", {"query": content}, mem_client=mem_client)
        assert result["count"] >= 1
        assert any(str(cluster_id) == str(c["id"]) for c in result["clusters"])
    finally:
        async with db_pool.acquire() as conn:
            # Clean up graph edges via DETACH DELETE
            await conn.execute("""
                SELECT * FROM cypher('memory_graph', $q$
                    MATCH (m:MemoryNode {memory_id: '%s'})
                    DETACH DELETE m
                $q$) as (result agtype)
            """ % memory_id)
            await conn.execute("DELETE FROM clusters WHERE id = $1", cluster_id)
            await conn.execute("DELETE FROM memories WHERE id = $1", memory_id)


async def test_tool_get_procedures(mem_client, db_pool):
    test_id = get_test_identifier("tool_proc")
    content = f"Procedure {test_id}"
    mid = await mem_client.remember(
        content,
        type=MemoryType.PROCEDURAL,
        importance=0.6,
        context={"steps": ["a", "b"]},
    )
    try:
        result = await execute_tool("get_procedures", {"task": content}, mem_client=mem_client)
        assert result["count"] >= 1
        assert any(m["memory_id"] == str(mid) for m in result["procedures"])
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", mid)


async def test_tool_get_strategies(mem_client, db_pool):
    test_id = get_test_identifier("tool_strat")
    content = f"Strategy {test_id}"
    mid = await mem_client.remember(
        content,
        type=MemoryType.STRATEGIC,
        importance=0.6,
        context={"evidence": "x"},
    )
    try:
        result = await execute_tool("get_strategies", {"situation": content}, mem_client=mem_client)
        assert result["count"] >= 1
        assert any(m["memory_id"] == str(mid) for m in result["strategies"])
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE id = $1", mid)


async def test_tool_list_recent_episodes(mem_client):
    result = await execute_tool("list_recent_episodes", {"limit": 3}, mem_client=mem_client)
    assert "episodes" in result


async def test_tool_create_goal_and_queue_message(mem_client, db_pool):
    goal_result = await execute_tool(
        "create_goal",
        {"title": "Test goal", "priority": "queued", "source": "user_request"},
        mem_client=mem_client,
    )
    assert "goal_id" in goal_result

    message_result = await execute_tool(
        "queue_user_message",
        {"message": "hello", "intent": "status"},
        mem_client=mem_client,
    )
    assert message_result.get("queued") is True
    assert message_result.get("outbox_message", {}).get("kind") == "user"

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM memories WHERE id = $1::uuid AND type = 'goal'::memory_type",
            goal_result["goal_id"],
        )
