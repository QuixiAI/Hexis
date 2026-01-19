---
title: Apache AGE Performance Best Practices
description: Best practices for improving queries in Apache Age with Azure Database for PostgreSQL.
author: shreyaaithal
ms.author: shaithal
ms.reviewer: maghan
ms.date: 05/19/2025
ms.update-cycle: 180-days
ms.service: azure-database-postgresql
ms.topic: concept-article
ms.collection:
  - ce-skilling-ai-copilot
ms.custom:
  - build-2025
# customer intent: As a user, I want to understand how to improve performance of my graph queries in Azure Database for PostgreSQL.
---

# Best practices: indexing, AGE EXPLAIN, and data load benchmarks

Apache AGE supported by Azure Database for PostgreSQL, provides support for advanced graph processing and querying. However, achieving optimal query performance requires a thoughtful strategy for indexing and data loading. This guide outlines some best practices based on recent benchmarking results and technical insights.

## Indexing in Apache AGE

Indexing is pivotal for improving query performance, especially in graph databases. 

### Default behavior

By default, Apache AGE doesn't create indexes for newly created graphs. This necessitates explicit creation of indexes based on the nature of your queries and dataset.

### WHERE clause

In Apache AGE, the following queries are evaluated differently:

```sql
SELECT * FROM cypher('graph_name',
 $$
 MATCH (n:Customer {Name:'Alice'}) RETURN n
 $$)
AS (n agtype);
```

```sql
SELECT * FROM cypher('graph_name',
 $$
 MATCH (n:Customer) WHERE n.Name='Alice' RETURN n
 $$)
AS (n agtype);
```

To take full advantage of indexing, you must understand which types of indexes are utilized by queries with and without a WHERE clause.

## EXPLAIN in Apache AGE

Unlike standard SQL, the EXPLAIN keyword in Cipher queries requires a different query format.

```sql
SELECT * FROM cypher('graph_name',
 $$
 EXPLAIN
 MATCH (n:Customer)
 WHERE n.Name='Alice'
 RETURN n
 $$)
AS (plan text);
```

```output
QUERY PLAN
--------------------------------------------------------------------------------------------------------------
Seq Scan on "Customer" n (cost=0.00..418.51 rows=43 width=32)
Filter: (agtype_access_operator(VARIADIC ARRAY[properties, '"Name"'::agtype]) = '"Alice"'::agtype)
```

To see the differences of query plans without WHERE clause:

```sql
SELECT * FROM cypher('graph_name',
 $$
 MATCH (n:Customer {Name:'Alice'}) RETURN n
 $$)
AS (n agtype);
```

```output
QUERY PLAN
---------------------------------------------------------------
Seq Scan on "Customer" n (cost=0.00..396.56 rows=9 width=32)
Filter: (properties @> '{"Name": "Alice"}'::agtype)
```

## Common index types

- **BTREE Index**: Effective for exact matches and range queries. Recommended for use with columns like ID, start_id, and end_id in edge and vertex tables.
- **GIN Index**: Useful for JSON fields, enabling efficient searches for key-value pairs in the properties column.

Use the following commands to create indexes for vertex and edge tables:

- Vertex table:

  ```sql
  CREATE INDEX ON graph_name."VLABEL" USING BTREE (id);
  CREATE INDEX ON graph_name."VLABEL" USING GIN (properties);
  ```
    
- Microsoft Edge table:

  ```sql
  CREATE INDEX ON graph_name."ELABEL" USING BTREE (id);
  CREATE INDEX ON graph_name."ELABEL" USING GIN (properties);
  ```
  
  ```sql
  CREATE INDEX ON graph_name."ELABEL" USING BTREE (start_id);
  CREATE INDEX ON graph_name."ELABEL" USING BTREE (end_id);
  ```

### Indexing a specific key-value in properties

For targeted queries, a smaller, more efficient BTREE index can be created for specific keys within the properties column:

```sql
CREATE INDEX ON graph_name.label_name USING BTREE (agtype_access_operator(VARIADIC ARRAY[properties, '"KeyName"'::agtype]));
```

This approach avoids indexing unnecessary data, improving efficiency.

## Query plan insights with EXPLAIN

The EXPLAIN keyword reveals how queries utilize indexes. Not all queries automatically use indexes, particularly those issued without a WHERE clause. Use EXPLAIN to verify index usage and optimize queries accordingly.

## Benchmark observations

Recent tests highlight the impact of indexing on query performance.

### Indexed vs. nonindexed queries

This section discusses the performance differences between indexed and nonindexed queries.

- Sequential scans outperform index scans for queries retrieving entire tables.
- Indexing significantly improves performance for join queries (for example, relationship counts).

## Data loading best practices

Efficient data loading is crucial for large datasets.

The AGEFreighter library offers a streamlined process for data ingestion.

### Data loading with AGEFreighter

AGEFreighter is a Python library designed to facilitate the loading of data into Apache AGE. It supports various data formats, including CSV, Avro, and Parquet, and provides a simple interface for loading data into AGE graphs.

#### Environment setup

- Created an Azure Database for PostgreSQL flexible server instance with AGE enabled.
- Python dependency management tool such as Poetry is recommended. Python 3.9 or later must be installed.
- The AGEFreighter library (AGEFreighter PyPi) must be installed as a dependency:

```bash
poetry add agefreighter
```

#### Use the CSV data format for benchmarks

For the benchmarks, I used a CSV file to load data into AGE. The CSV format is widely supported and easy to work with, making it a good choice for data loading tasks.

Since the dataset used consists of legal cases and relationships between them, I structured my input CSV file as follows:

```csv
id,CaseID,start_vertex_type,end_CaseID,end_vertex_type
1,1005631,Case,5030916,Case
2,1005631,Case,5028652,Case
3,1005631,Case,996512,Case
4,1005631,Case,3413065,Case
5,1005631,Case,4912975,Case
```

Each row represents a relationship between two cases. CaseID refers to the starting case node, while end_CaseID refers to the connected case.

#### Use the data loading script for benchmarks

The following Python script was used to load my dataset into AGE. You might also refer to "Usage of CSVFreighter" section in AGEFreighter PyPi for a different example.

```python
await instance.load(
 graph_name="CaseGraphFull",
 start_v_label="Case",
 start_id="CaseID",
 start_props=[],
 edge_type="REF",
 edge_props=[],
 end_v_label="Case",
 end_id="end_CaseID",
 end_props=[],
 csv_path="./cases.csv",
 use_copy=True,
 drop_graph=True,
 create_graph=True,
 progress=True,
)
```
As you can see, the graph_name and fields in the provided csv file is defined here. The use_copy=True parameter ensures efficient data loading. The drop_graph=True and create_graph=True parameters ensure a fresh start before loading new data.

### Other data sources

AGEFreighter supports other formats such as MultiCSV, Avro, Parquet, Azure Storage, etc. which can be adapted based on data format requirements. You can get more information here: ['AGEFreighter PyPi'](https://pypi.org/project/agefreighter/)

## Data loading performance benchmarks

- Dataset size: 725K cases, 2.8M relationships.
- Loading time: 83 seconds.

Efficient data loading is essential for handling large datasets effectively. 

> [!NOTE]
> While suitable for large files, this process might be less effective for smaller datasets due to preparation time.

## Related content

- [Azure Database for PostgreSQL documentation](../overview.md)
- [PostgreSQL extension for Visual Studio Code](https://marketplace.visualstudio.com/items?itemName=ms-ossdata.vscode-postgresql)
- [GitHub Copilot extension](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot)
- [GitHub Copilot Chat extension](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat)
- [MSSQL extension for Visual Studio Code?](/sql/tools/visual-studio-code-extensions/mssql/mssql-extension-visual-studio-code)

Optimizing Apache AGE Graph Performance on PostgreSQL 16
Apache AGE (A Graph Extension) enables graph queries (via openCypher) on PostgreSQL. In a knowledge graph use case on Postgres 16 – especially with all graph access encapsulated in SQL functions – achieving optimal performance requires careful use of query analysis tools, indexing, and configuration tuning. Below is an in-depth guide covering EXPLAIN usage, indexing strategies, clever workarounds, Postgres configuration tweaks, and benchmarking practices for Apache AGE.
Using EXPLAIN and EXPLAIN ANALYZE for AGE Queries
Understanding Query Plans: To analyze a Cypher query’s execution plan in AGE, you include the EXPLAIN (or EXPLAIN ANALYZE) keyword inside the cypher() function call (as part of the Cypher query string) rather than in front of the SQL call. For example, to get a plan for a simple pattern match:
SELECT * 
FROM cypher('graph_name', $$
  EXPLAIN ANALYZE 
  MATCH (n:Customer)
  WHERE n.Name = 'Alice'
  RETURN n
$$) AS (plan text);
This returns the query plan (as text) with execution details and timing[1][2]. The plan will show how AGE translated the Cypher pattern into underlying SQL operations. For instance, a plan might reveal a sequential scan on a vertex table with a filter applied, or an index scan if applicable. In the example above, the plan shows a Seq Scan on the "Customer" table with a filter on the Name property[3]. By contrast, using a pattern literal property filter (e.g. MATCH (n:Customer {Name:'Alice'})) yields a slightly different filter condition (properties @> '{"Name": "Alice"}')[4] – an important distinction because it affects index usage (discussed below).
Analyzing Function Calls: If your graph queries are wrapped in SQL functions, you have a couple of options to examine their plans: you can call the function with EXPLAIN (e.g. EXPLAIN SELECT my_graph_query_fn(...);), or incorporate EXPLAIN inside the function’s Cypher query as shown above. In PostgreSQL, a simple SQL function will often be inlined into the calling query, allowing its plan to be shown directly. However, for more complex stored procedures (e.g. PL/pgSQL), you may not see the internal plan with a plain EXPLAIN. In such cases, consider PostgreSQL’s auto_explain extension to log plans of queries executed inside functions[5] (enable auto_explain.log_nested_statements so that even nested Cypher calls are captured). This is especially useful when you cannot easily modify the function to add EXPLAIN or when analyzing production workloads.
Interpreting Plans: Use EXPLAIN (no ANALYZE) for a quick look at the planned operations and whether indexes are being used, and EXPLAIN ANALYZE for actual runtime metrics. The output will include the estimated cost, rows, and width, as well as (with ANALYZE) the actual rows returned and time taken by each step. You can also append options like EXPLAIN (ANALYZE, BUFFERS) to see buffer hits and I/O, which helps determine if the query is disk I/O bound[6]. The key is to verify that the planner is doing what you expect – for example, using an index scan versus a sequential scan – and to identify any bottleneck steps (like a nested loop over many rows or a large sort). Since AGE translates Cypher to SQL under the hood, the plan will show joins and filters on the underlying tables (e.g. a join filter using start_id/end_id for relationships, or a function like age_match_vle(...) for variable-length pattern matches). By examining these, you can often infer where to add indexes or how to rewrite the query.
Example: Suppose you have a function find_neighbors(person_id) that runs a Cypher query to get a person’s friends. To profile it, you might do:
EXPLAIN ANALYZE SELECT find_neighbors(123);
If the function is simple and marked as IMMUTABLE/STABLE (encouraging inlining), the plan could show the internal index scans on edges. If not, you might only see a generic “Function Scan” in the plan. In that case, you’d temporarily modify the function or use the auto_explain approach to get detailed insight. In summary, always use the Cypher-embedded EXPLAIN to validate how AGE executes your query, especially since Cypher queries wrapped in functions might hide complexity. As the official guidance notes: “Use EXPLAIN to verify index usage and optimize queries accordingly”[7].
Indexing Strategies for Graph Pattern Queries
By default, Apache AGE does not create any indexes on graph data – all vertices and edges are stored in PostgreSQL tables without automatic indexes[8]. For a large-scale knowledge graph, proper indexing is absolutely critical to avoid slow sequential scans on every query. Here are key indexing strategies:
•	Primary ID Indexes: Ensure that each vertex and edge table has a B-tree index on its id. Typically, the id column uniquely identifies a vertex or edge. A B-tree index on id allows fast lookup of specific nodes or relationships by ID[9]. If you created the graph and labels via AGE, you can manually add an index: CREATE INDEX ON graph_name."VertexLabel" USING BTREE (id); (and similarly for each edge label table)[10]. This is essential if your application frequently accesses specific nodes or needs to ensure uniqueness.
•	Relationship Key Indexes: Similarly, index the start_id and end_id columns of edge tables with B-trees[11]. These indexes allow the planner to quickly retrieve all edges outgoing from or incoming to a given vertex. Graph traversals (e.g. MATCH (a)-[r:REL]->(b)) under the hood translate to join conditions like r.start_id = a.id AND r.end_id = b.id. With indexes on start_id/end_id, these joins can use index scans or index lookups instead of scanning the entire edge table for each step. This dramatically improves performance for queries that expand from a node to its neighbors, especially when each node has many connections. In a knowledge graph with high degree nodes, an index on start_id can be the difference between a sub-second traversal and a full-table scan taking many seconds.
•	Property Indexes (GIN for agtype): Vertex and edge properties in AGE are stored as an agtype (a JSON-like container). Creating a GIN (Generalized Inverted Index) on the properties column of each label table is highly recommended[9]. A GIN index on the JSON/agtype enables efficient search by key/value, especially for containment queries. For example, a query with a pattern MATCH (p:Person {name: "Bob"}) or a WHERE clause p.name = 'Bob' will internally use a JSON containment or extraction operator. With a GIN index on properties, a filter like properties @> '{"name": "Bob"}' can use an index to quickly find relevant nodes[12]. Without it, such queries degrade to sequentially scanning every vertex. On large graphs, that’s untenable. Thus, add indexes like: CREATE INDEX ON graph_name."Person" USING GIN (properties); for each frequently queried label[10]. Likewise for edges if you query edge properties.
•	Indexing Specific Properties: Often, not all properties need indexing – just the ones commonly used in query filters (e.g. names, types, timestamps, etc.). You can create a targeted functional index on a specific key within the properties JSON to optimize those queries. Apache AGE provides an agtype_access_operator to extract a key’s value. For example, to index the “Name” field of a Customer node:
CREATE INDEX ON graph_name."Customer" 
  USING BTREE (agtype_access_operator(VARIADIC ARRAY[properties, '"Name"'::agtype]));
This index stores the Name value for each node, allowing equality searches (n.Name = 'Alice') to use a fast b-tree lookup[13]. The official guidance notes this “avoids indexing unnecessary data, improving efficiency”[14]. Use such functional indexes for any high-selectivity property that you query often (e.g. an external ID, a category, etc.). In practice, a GIN index on the whole properties may be sufficient for many cases, but a b-tree on a specific field can be smaller and faster if that field is a frequent filter.
•	Clustered Index or Partitioning: If your data has a natural partition key (e.g. by time, by entity type, or by ID range), consider partitioning the underlying tables. PostgreSQL 16 allows range or hash partitioning on tables used by AGE. Partitioning can “divide the data into smaller, more manageable chunks” so that queries only scan relevant partitions[15]. For example, you might partition a huge Event vertex table by year or a Transaction edge table by region. This not only limits scan scope (improving cache locality) but also lets PostgreSQL execute parallel index scans on multiple partitions for a single query. Parallelism: With partitioning, a query that needs to touch many rows can spawn parallel workers to scan partitions in parallel, often speeding up response times on large graphs[16]. Ensure your max_parallel_workers_per_gather is tuned (see below) and partitioning is done on an attribute that aligns with common query filters (e.g. queries by date range benefit from date-based partitions). The dev team has demonstrated simple ID-range partitioning of vertex tables as a way to scale to large datasets[17].
•	Maintain Indexes: Just as with any database, monitor index usage and update statistics. For very large bulk loads or updates, you may need to REINDEX or ANALYZE to ensure the planner has up-to-date information. PostgreSQL’s auto-analyze should kick in, but on multimillion-row tables, be mindful of bloat – VACUUM periodically if you do heavy updates/deletes on the graph.
Verify Index Usage: After creating indexes, always re-run EXPLAIN on your key queries to confirm they’re being used. Not all Cypher queries automatically take advantage of indexes – for example, a pattern without a WHERE clause might still do a seq scan[18]. The AGE planner might not use an index if the query pattern doesn’t filter by a selective property. As the Azure PostgreSQL team notes, “Not all queries automatically use indexes, particularly those issued without a WHERE clause”[19]. Use hints from the plan: if you still see Seq Scan on a large table where you expected an index, you may need to rewrite the query (e.g. add an explicit property filter, or avoid using functions that prevent index usage) or create a different index. A common situation is the difference between using a literal property match in the pattern vs. a separate WHERE clause: the former uses a JSON containment (properties @> {...}) which GIN can support[12], whereas the latter might use a function equality check[3], requiring a functional index to optimize. Align your query style with your indexing strategy.
In summary, identify the access patterns of your knowledge graph (by name? by type? by relationship?) and create the appropriate indexes. Large knowledge graphs often benefit from a combination of GIN indexes (for flexible property searches) and b-tree indexes (for exact matches, IDs, and foreign keys like start/end ids). Proper indexing can “significantly speed up query performance” in AGE[20] – often turning seconds-long queries into millisecond responses if the index eliminates a full scan.
Performance Tricks, Workarounds, and Undocumented Boosters
Beyond straightforward indexing, experienced AGE users employ a variety of techniques to squeeze more performance out of graph queries:
•	Leverage Caching: For read-heavy knowledge graph applications, caching can offer huge wins. Because AGE runs on PostgreSQL, you benefit from the DB’s page cache (shared buffers and OS cache). But you can also cache results at the application level or via an external in-memory store. In fact, Apache AGE can integrate with in-memory caching frameworks like Apache Ignite to cache frequently accessed subgraphs or query results in memory[21]. This means if certain expensive pattern queries are run often (and the underlying data doesn’t change frequently), you can store the result set in a fast cache and serve repeated queries from there, bypassing recomputation. Even without a formal integration, you can use something like Redis or a simple in-process cache in your application layer to hold the results of common queries. The effect is reduced disk I/O and latency for those queries, at the cost of extra memory and potential staleness. Tip: If you cache, devise an invalidation strategy (e.g. refresh or invalidate on relevant data changes) to ensure correctness. Caching is especially useful for global analytics or subgraph traversals that are expensive to compute but needed often.
•	Prepared Statements & Plan Reuse: Apache AGE supports prepared statements with Cypher, which can help if you execute the same query pattern repeatedly with different parameters. Instead of reparsing and planning the Cypher every time, you can prepare it once. For example, you can prepare a parameterized Cypher query using PostgreSQL’s PREPARE:
PREPARE getPersonByName(agtype) AS
SELECT * 
FROM cypher('myGraph', $$
    MATCH (v:Person) 
    WHERE v.name = $name 
    RETURN v
$$, $1) AS (v agtype);
Then execute it with EXECUTE getPersonByName('{"name": "Alice"}');[22][23]. This way, the Cypher query is planned once, and subsequent executions reuse the execution plan, saving parsing and planning overhead. In a workload with lots of similar graph queries (common in knowledge graph lookups by ID or attribute), this can shave off milliseconds and also reduce CPU load on the database. If your application uses a PostgreSQL driver, consider using server-side prepared statements or the extended query protocol for repeating Cypher calls. (Note: As of current versions, the performance gain from plan caching might be modest compared to overall query time on large data, but it’s still a recommended practice for frequently run queries.)
•	Query Rewriting and Decomposition: Sometimes a single Cypher query can be rewritten or broken into steps for better performance. The AGE engine does have some “query rewriting capabilities”, but you often need to optimize manually[24]. For example, if you have an extremely complex pattern or a deep traversal, consider whether you can limit the search space first. You might first fetch a set of candidate vertices with a cheap indexed lookup, and then feed those into a second Cypher query for the graph traversal. One way is using the SQL-in-Cypher integration: you could write a user-defined SQL function to pre-filter or retrieve IDs, then call that in a Cypher WHERE clause[25][26]. Another approach is using Cypher in a CTE/JOIN: for instance, do a SQL query to get a list of relevant IDs (using traditional SQL where it excels) and then join that with a Cypher subquery that expands the graph around those IDs. While AGE doesn’t allow embedding raw SQL inside Cypher directly, these UDF and CTE tricks serve a similar purpose – leveraging the relational engine for what it’s good at (set operations, large aggregations) and narrowing the graph query to a smaller subset.
Undirected or optional pattern optimization: If you use OPTIONAL MATCH or undirected edges (e.g. MATCH (a)-[r]-(b) without direction), be aware that AGE’s translation may result in join filters with OR conditions (checking both directions), which can’t use indexes efficiently[27][28]. A workaround is to split such a query into two directed parts (one for out-edges and one for in-edges) and UNION them, if possible, or to restructure the query logic. Similarly, deeply nested OPTIONAL matches can blow up intermediate result sets – sometimes it’s better to query in parts or use WHERE EXISTS subqueries to avoid massive optional expansions.
•	Limit the Search Space Early: Always try to use specific patterns or WHERE clauses to constrain matches as early as possible in the query. For example, if you’re querying a knowledge graph for a pattern like (author:Person)-[:WROTE]->(book:Book), and you only need authors from a certain country, include that filter in the MATCH or a preceding WITH clause to reduce the number of authors considered. This sounds obvious, but in Cypher it’s easy to write a MATCH that finds a large subgraph and only later filter by some property. Pushing filters closer to the source (early in the query) means less work for the database. The “Scaling Apache AGE” guide emphasizes using specific MATCH patterns to filter data early and minimizing use of broad OPTIONAL MATCH clauses that generate large intermediate results[29]. If you only need a top N results, use LIMIT to cut off exploration as soon as possible as well.
•	Avoid Cartesian Products and Deep Nesting: Be cautious of Cypher queries that inadvertently cause a Cartesian product (e.g. two unrelated MATCH patterns in the same query without a linking condition) – this can multiply result rows and work. Either split such queries or introduce a relationship between the patterns. Also, “avoiding excessively complex or deeply nested queries” is a known best practice[30]. A query that goes 5 or 6 hops deep with multiple filtering conditions and subqueries might strain the planner and executor. If you find the planner’s estimates are way off (check EXPLAIN ANALYZE row counts vs actual), you might benefit from breaking the query into smaller pieces or using temporary tables to hold intermediate results.
•	Graph-Specific Optimizations: If your knowledge graph has certain patterns (like a star schema or hierarchy), exploit them. For example, in a hierarchy you might maintain an explicit table of ancestor relations (transitive closure) to answer ancestor/descendant queries in O(1) time[31][32], instead of doing recursive traversals frequently. This is essentially caching the results of a traversal in a table, trading storage for speed. One team precomputed transitive closures with triggers for their tree structure, massively speeding up “within N degrees” queries at the cost of storage and maintenance complexity[33][34]. This kind of denormalization may or may not fit your use case, but it’s worth knowing that if pure Cypher is too slow for a particular analytic query, you might achieve big gains by redesigning the data model (even if it means some duplication).
•	Use Latest AGE Release: Ensure you’re running the latest version of Apache AGE compatible with PostgreSQL 16. Performance fixes and improvements are continually added. For example, newer versions might improve the Cypher planner or add support for parallelism. Staying up-to-date gives you the benefit of any optimizations the community has introduced[35].
In essence, treat Apache AGE performance tuning as a multifaceted problem: use caching for repeated heavy queries, reuse plans where possible, and don’t hesitate to mix Cypher with SQL or custom logic if it avoids known pitfalls. Many of these are “undocumented” in the sense of tribal knowledge from the community – e.g., one user notes avoiding deep traversals beyond 3-4 hops if possible, or else a native graph DB might outperform AGE[36]. With careful design, AGE can handle surprisingly large and complex graph queries, but you sometimes need to guide it with the tricks above.
PostgreSQL Configuration Tuning for Graph Workloads
Because AGE sits atop PostgreSQL, the normal arsenal of PostgreSQL performance tuning applies. Tuning the database parameters for a graph workload (which can involve large join-like operations and heavy use of memory for searches) is crucial. Below are some of the most relevant settings to consider:
•	Memory Settings: Increase memory allocations to handle large graphs in memory as much as possible.
•	shared_buffers – This controls how much memory is used for PostgreSQL’s buffer cache. For a dedicated server, something like 25% to 40% of RAM is often recommended. Larger shared_buffers means more of your vertex/edge tables (and indexes) can reside in memory, reducing disk I/O. For example, you might set shared_buffers = 4GB on a system with ~16GB RAM[37][38]. Ensure the OS has enough file system cache as well; effective cache is combined between shared_buffers and OS cache.
•	work_mem – Each sort or hash operation in a query can use up to this amount of memory before spilling to disk. Graph queries that do aggregations (COUNT, etc.), perform hash joins, or sort large result sets can benefit from more work_mem. The default (4MB or so) is low for big data; you might raise it to 64MB or more, keeping in mind this is per operation per user. In AGE, a MATCH that needs to hash a large edge set or sort results will use this. The Azure team suggests increasing work_mem to improve sorting/hashing performance[39]. Example setting: work_mem = 64MB[37][38].
•	maintenance_work_mem – If you’ll be bulk loading or creating indexes on large tables (typical when initially building the knowledge graph), bump this up (hundreds of MB or a few GB) to speed up index creation and vacuum operations. This doesn’t directly affect query speed, but it can make index creation and bulk loading much faster.
•	Planner Cost Settings: PostgreSQL’s planner has cost parameters that influence decision-making.
•	effective_cache_size – Set this to reflect the total memory PostgreSQL can expect to use for caching (shared_buffers + OS cache). A higher effective_cache_size makes the planner more likely to choose index scans (since it assumes more of the index/table might be cached in RAM). For a machine with, say, 16GB memory and 4GB shared_buffers, you might set effective_cache_size = 12GB[37][38]. This isn’t a hard memory allocation, just a hint to the planner.
•	random_page_cost – If using SSDs, you can lower this (from the default 4.0) to something like 1.1–1.5 to indicate random I/O is cheap. This again makes index usage more favorable, which can help graph queries that do many index lookups (like hopping from one vertex to many neighbors). However, be cautious: if you set it too low, the planner might use index scans when a sequential scan would actually be better (e.g., scanning almost an entire table).
•	jit – PostgreSQL 16 has JIT (Just-In-Time compilation) for query execution. JIT can sometimes speed up heavy computation in queries (like complex expressions or big hash joins), but it adds overhead to planning. For small quick queries, turning JIT off can save that overhead. For very large graph queries that run for seconds and crunch a lot of data, JIT might help. You can experiment by toggling jit = on/off and measuring with EXPLAIN ANALYZE. In PG16, JIT is enabled by default when beneficial, but if you see planning times dominating, try disabling it to compare.
•	Parallel Execution: Many graph queries involve scanning large tables (especially when you don't have selective filters). PostgreSQL can use multiple CPU cores to speed up such scans and joins if configured.
•	max_parallel_workers_per_gather – Increase this to allow more parallel workers for a single query. For instance, setting max_parallel_workers_per_gather = 4 allows the planner to use up to 4 extra worker processes for parallelizable operations[39][38]. This is very useful for analytic-style graph queries that touch a significant portion of the graph (e.g. “find the top 10 nodes with highest degree” or a broad pattern match). With proper indexes and partitioning, a parallel index scan on edges can greatly speed up traversal heavy queries. Also ensure max_parallel_workers (total) is set high enough (at least as high as the per-gather times number of concurrent queries you expect).
•	parallel_tuple_cost and parallel_setup_cost – these can be adjusted (lowered slightly) if you find the planner is not using parallel when it probably should. They control how expensive the planner thinks parallelism is. In a graph workload, we often want parallelism for heavy queries, so making it more likely can help.
•	Disk and WAL Settings:
•	If you do bulk loads or large updates to the graph, consider tuning checkpoint settings to avoid pauses, and WAL (write-ahead log) settings to balance safety vs throughput. For instance, in a load stage, you might set synchronous_commit = off (trading a bit of safety for speed) and later turn it on for normal operation.
•	Ensure wal_buffers is sufficiently high (but PostgreSQL usually auto-tunes this based on shared_buffers in modern versions).
•	If your knowledge graph is mostly read-only after initial load (many are), you can also tweak autovacuum to be less aggressive (or even off) during the load and then do a manual vacuum after.
•	Monitoring and Planner Feedback:
•	Enable the pg_stat_statements extension. This will track execution stats for all queries, including those involving AGE. It can help you identify which Cypher queries (or underlying SQL queries) are using the most time or resources, so you know what to focus on.
•	Consider using EXPLAIN (ANALYZE, BUFFERS) on sample queries to observe not just timing but how they use I/O. If buffers shown as read from disk are high, you may need more RAM allocated or better indexing. If CPU time is the main issue, look at reducing data processed or increasing parallelism.
•	Keep an eye on workload-specific GUCs: for instance, track_io_timing (if you want to measure I/O timings) or temp_file_limit (to catch runaway queries that spill to disk).
In summary, tune PostgreSQL as you would for a large analytical workload: more memory, parallel workers, and appropriate cost settings to encourage index and parallel usage. The combination recommended in one guide for large AGE graphs was:
shared_buffers = 4GB
work_mem = 64MB
effective_cache_size = 12GB
max_parallel_workers_per_gather = 4
[39][40]
These values are just examples – you should adjust based on your server’s hardware and the size of your data. Always test the impact of changes; for instance, increasing work_mem can improve a single query’s speed but if too many concurrent queries use it, you could exhaust RAM.
Don’t forget the basics: a fast disk (SSD/NVMe) for storage, and ensuring your operating system is tuned for PostgreSQL (e.g., no Linux overcommit issues for large shared_buffers, etc.). And if your dataset is extremely large (billions of edges) and one machine isn’t enough, you could explore horizontal scaling – Apache AGE data lives in regular tables, which means extensions like Citus can partition those tables across nodes[41]. A distributed approach is advanced, but it’s an option if you truly need it.
Performance Testing and Benchmarking for Apache AGE
When optimizing, measure everything. Develop a methodology for testing AGE’s performance in your specific use case:
•	Use Realistic Data and Queries: The performance on a knowledge graph can vary with graph structure (degree distribution, etc.). Try to benchmark with data that’s close to your production graph. If that’s not possible early on, use a generator to mimic the graph (for example, a scale-free network generator if your data behaves like a social network). Ingest that into AGE and run the representative queries.
•	EXPLAIN ANALYZE on Key Queries: For each critical query (or function) in your workload, run EXPLAIN ANALYZE to get baseline execution times and plans. Note the biggest contributors to time. This will tell you if, say, a query is CPU-bound (long CPU time, maybe no disk reads if cached) or I/O-bound (lots of disk hits, or waiting). It will also highlight if an index is being used effectively or if the row estimates are way off (which can indicate a need to ANALYZE or adjust planner settings).
•	Benchmark Indexing Impact: The difference indexing makes is often huge. As reported in Microsoft’s tests, “Indexing significantly improves performance for join queries (for example, relationship counts)”[42]. For instance, they observed that a relationship count query (joining vertices to edges) was much faster with the proper indexes. In your own benchmarking, try running a few queries with and without certain indexes to quantify the benefit. Similarly, note that “sequential scans outperform index scans for queries retrieving entire tables”[42] – this means if a query isn’t selective at all, an index might not help and could even hurt (due to overhead), so don’t be alarmed if the planner chooses a seq scan in such cases. The goal is to ensure indexes are in place for selective operations, and to understand which queries benefit from them.
•	Throughput and Concurrency Testing: If your use case involves concurrent access (multiple users querying the graph at once), use a tool like pgbench or custom scripts to simulate load. Measure not just single-query latency but throughput (queries per second the system can handle) and how latency changes under load. Knowledge graph queries might contend on CPU or I/O; monitoring tools (like htop, iostat, or Postgres’s own pg_stat_* views) during load tests can reveal bottlenecks (e.g. all CPUs maxed out, or high disk wait times).
•	Graph-Specific Benchmarks: Look into graph query benchmarks such as the LDBC Social Network Benchmark (SNB). While SNB is for Neo4j/other graph DBs, you can attempt to implement its queries in AGE and see how it performs. This can give you a sense of how AGE scales with complex analytical queries. The community may have published comparisons (for example, internal testing comparing Cypher vs SQL on a Goodreads dataset[43][44] showed Cypher was ~15× slower for a heavy aggregation with ordering in one case, indicating room for improvement). Use such insights to guide your optimization – e.g., in that case, the ORDER BY on an aggregate was the culprit; removing it improved performance[45]. If you encounter a query that AGE handles poorly, consider alternatives (like doing the aggregation in SQL or in batches).
•	Track Progress and Changes: When you adjust configurations or rewrite queries, use consistent benchmarks to see the effect. It’s “crucial to benchmark different optimization techniques and configurations to measure their impact on performance”[46]. Keep a log of query timings (perhaps using a spreadsheet or a simple table in the database) for each change you make. This helps ensure your tuning is actually making things better, and not just shifting the burden around.
•	Use Profiling Tools: Besides EXPLAIN ANALYZE, you can use pg_stat_statements to get cumulative times and even standard deviation of query times over many executions – great for spotting variability. Also, consider enabling log_min_duration_statement (to log slow queries) during testing to catch any outliers. For long-running analytical queries, the pg_stat_progress_* views might show you what phase a command is in (useful for debugging large index builds or vacuum, for instance).
•	Testing in Functions: If your queries run via functions, you might write wrapper scripts to execute them many times and measure the average time. Or use the PostgreSQL \timing in psql for quick ad-hoc measurement. Ensure you test both cold cache (after a pg_prewarm or restart to simulate no cache) and warm cache scenarios – real workloads might hit the database with warm caches most of the time, but if your graph is larger than RAM, some I/O will always be involved. Observing both can guide whether to invest in more memory or better disks.
Finally, engage with the community. Apache AGE is evolving, and performance is a known focus area. Reading through GitHub issues and discussions can provide insight into current limitations or upcoming improvements. For instance, if an issue highlights a slow pattern (like undirected edge matching or very deep variable-length searches), you might find recommended workarounds or at least be aware that “this query is slow by design in the current version.” If your use case pushes the limits, consider contributing: sometimes a custom extension or tweaking the AGE source for your patterns could be an option (it is open source, after all).
Summary: Optimizing Apache AGE on Postgres 16 involves using SQL-world tools (EXPLAIN, indexes, tuning GUCs) in tandem with graph-specific knowledge (how Cypher patterns translate under the hood). By systematically analyzing query plans, creating the right indexes, applying thoughtful workarounds, and tuning the server, you can achieve substantial performance gains – often making AGE suitable for large knowledge graph workloads that demand real-time query responses. Remember that optimization is iterative: monitor performance, identify bottlenecks, apply one change at a time, and measure again[47]. With patience and careful tuning, you can build a snappy, scalable graph application on top of PostgreSQL and Apache AGE.
Sources:
•	Apache AGE documentation and user guides on indexing and query analysis[9][1][13]
•	Microsoft Azure PostgreSQL team’s best practices for AGE[48][42]
•	Community Q&A and articles illustrating EXPLAIN usage and performance tricks[2][21][22]
•	“Scaling Apache AGE” and related DEV guides on large dataset tuning (partitioning, config)[17][37]
•	Reddit and Stack Overflow discussions on real-world AGE performance and comparisons[36][44]
 
[1] [3] [4] [7] [8] [9] [10] [11] [12] [13] [14] [18] [19] [42] [48] Apache AGE Performance Best Practices | Microsoft Learn
https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-age-performance
[2] postgresql - Measuring Query Execution Time in Apache AGE Using AGE-Viewer-GO - Stack Overflow
https://stackoverflow.com/questions/76301241/measuring-query-execution-time-in-apache-age-using-age-viewer-go
[5] amazon rds - Getting the query plan for statements inside a stored procedure in PostgreSQL - Stack Overflow
https://stackoverflow.com/questions/71314769/getting-the-query-plan-for-statements-inside-a-stored-procedure-in-postgresql
[6] PostgreSQL Showdown: Complex Joins vs. Native Graph Traversals with Apache AGE | by Sanjeev Singh | Medium
https://medium.com/@sjksingh/postgresql-showdown-complex-joins-vs-native-graph-traversals-with-apache-age-78d65f2fbdaa
[15] [17] [29] [37] [38] [39] [40] [41] Scaling Apache AGE for Large Datasets: A Guide on How to Scale Apache AGE for Processing Large Datasets - DEV Community
https://dev.to/humzakt/scaling-apache-age-for-large-datasets-a-guide-on-how-to-scale-apache-age-for-processing-large-datasets-3nfi
[16] [31] [32] [33] [34] Apache AGE for nodes within n degrees of connection OR Neo4j? : r/PostgreSQL
https://www.reddit.com/r/PostgreSQL/comments/1fgvmjb/apache_age_for_nodes_within_n_degrees_of/
[20] [30] [35] Optimization Techniques in Apache AGE - DEV Community
https://dev.to/maruf13/optimization-techniques-in-apache-age-51o0
[21] [24] [46] [47] Maximizing Real-time Analytics Performance with Apache Age's Query Optimization - DEV Community
https://dev.to/moiz697/maximizing-real-time-analytics-performance-with-apache-ages-query-optimization-2jc4
[22] [23] Prepared Statements — Apache AGE master documentation
https://age.apache.org/age-manual/master/advanced/prepared_statements.html
[25] [26] Managing and querying time-based events in Apache AGE graph database - Stack Overflow
https://stackoverflow.com/questions/76104856/managing-and-querying-time-based-events-in-apache-age-graph-database
[27] [28] database - Same query plan of AGE and different stacktrace (PostgreSQL version 15 & 16) - Stack Overflow
https://stackoverflow.com/questions/77017988/same-query-plan-of-age-and-different-stacktrace-postgresql-version-15-16
[36] Apache AGE performance : r/apacheage
https://www.reddit.com/r/apacheage/comments/1byu6io/apache_age_performance/
[43] [44] [45] Major Performance Difference: SQL vs. Cypher for Aggregation/Ordering · Issue #2194 · apache/age · GitHub
https://github.com/apache/age/issues/2194
