import os
from uuid import uuid4
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI
from app.core.config import settings
from fastembed import TextEmbedding
import io

class RAGService:
    def __init__(self):
        self.qdrant = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            timeout=20.0,
        )
        self.collection_name = settings.QDRANT_COLLECTION
        self._ensure_collection()
        
        # Initialize OpenAI
        if settings.OPENAI_API_KEY == "sk-proj-placeholder":
             print("WARNING: OPENAI_API_KEY is not set. Chatbot will fail.")
        
        self.llm = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            # No base_url needed for official OpenAI
            timeout=10.0,
            max_retries=2,
        )
        
        # Lazy-load embedding model to reduce startup memory
        self._embedding_model = None

    @property
    def embedding_model(self):
        """Lazy-load the embedding model only when needed"""
        if self._embedding_model is None:
            import os
            from fastembed import TextEmbedding
            cache_dir = "/tmp/fastembed_cache"
            os.makedirs(cache_dir, exist_ok=True)
            self._embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_dir)
        return self._embedding_model

    def _ensure_collection(self):
        try:
            # Check existing collection
            info = self.qdrant.get_collection(self.collection_name)
            # If dimensions don't match 384 (BGE-small), recreate
            # Handle both object access and dict access for safety
            vectors_config = info.config.params.vectors
            size = None
            if hasattr(vectors_config, "size"):
                size = vectors_config.size
            elif isinstance(vectors_config, dict) and "size" in vectors_config:
                size = vectors_config["size"]
            
            if size is not None and size != 384:
                self.qdrant.delete_collection(self.collection_name)
                raise Exception("Recreate")
        except Exception as e:
            # Collection check error
            try:
                self.qdrant.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
                )
            except:
                pass

        # Create payload index for tenant_id (Required for filtering)
        try:
            self.qdrant.create_payload_index(
                collection_name=self.collection_name,
                field_name="tenant_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass

    def _embed(self, text: str) -> list[float]:
        # Generate embeddings using FastEmbed
        embeddings = list(self.embedding_model.embed([text]))
        return embeddings[0].tolist() 

    async def ingest_document(self, tenant_id: str, filename: str, content: bytes, mime_type: str):
        # Offload blocking CPU/IO work to a thread
        import asyncio
        loop = asyncio.get_running_loop()
        
        await loop.run_in_executor(None, self._ingest_sync, tenant_id, filename, content, mime_type)

    def _ingest_sync(self, tenant_id: str, filename: str, content: bytes, mime_type: str):
        text = ""
        try:
            if mime_type == "application/pdf":
                import io
                import pypdf
                pdf_file = io.BytesIO(content)
                reader = pypdf.PdfReader(pdf_file)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            else:
                # Fallback / Text
                text = content.decode("utf-8", errors="ignore")
        except Exception as e:
            return 



        if not text.strip():
            return

        # Chunking
        chunks = [text[i:i+1200] for i in range(0, len(text), 1000)]

        
        points = []
        for i, chunk in enumerate(chunks):
            if not chunk.strip(): continue
            vector = self._embed(chunk)
            if not vector: continue
            
            points.append(models.PointStruct(
                id=str(uuid4()),
                vector=vector,
                payload={
                    "tenant_id": str(tenant_id),
                    "filename": filename,
                    "text": chunk,
                    "chunk_index": i
                }
            ))

        if points:
            try:
                self.qdrant.upsert(
                    collection_name=self.collection_name,
                    points=points
                )
            except Exception as e:
                print(f"Qdrant Upsert Error: {e}")


    async def query(self, tenant_id: str, question: str, history: list[dict], db: "AsyncSession", user_ctx: "AuthContext") -> str:
        # Quick greeting check
        clean_q = question.lower().strip(" .,!?")
        if clean_q in ["hello", "hi", "hey", "hola", "greetings"]:
            return "Hello! I am your community AI assistant. I can help you with documents and database information."

        # --- Define Tools ---
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_documents",
                    "description": "Search the official HOA documents, rules, bylaws, and guidelines.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search query for documents"}
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_tables",
                    "description": "List all public tables in the database to understand what data is available.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "describe_table",
                    "description": "Get the schema (columns and types) of a specific table.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "table_name": {"type": "string", "description": "The name of the table to describe"}
                        },
                        "required": ["table_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sql_query",
                    "description": "Execute a read-only SQL query to retrieve data. ALWAYS start with 'SELECT'.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The SQL query to execute"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

        # --- System Prompt ---
        system_instructions = """
        You are an intelligent HOA Assistant with access to both documents and the database.
        
        Capabilities:
        1. **Document Search**: Use `search_documents` for rules, bylaws, and written guidelines.
        2. **Database Access**: You can inspect the database schema and query data directly.
           - Use `list_tables` to see what tables exist.
           - Use `describe_table` to understand table structure before querying.
           - Use `sql_query` to fetch specific data. ensure you filter by `tenant_id` if the table has it (most do).
        
        Constraints:
        - **Read-Only**: You can only read data. Do not attempt to modify, delete, or drop anything.
        - **Tenant Isolation**: ALWAYS filter your SQL queries by `tenant_id` = '{tenant_id}' for tables that have a `tenant_id` column.
        - **Privacy**: Do not reveal sensitive fields like password hashes or refresh tokens.
        - **Formatting**: ALWAYS use Markdown to format your response.
          - Use **tables** for structured data with multiple columns (e.g., lists of users, units).
          - Use **bullet points** for simple lists.
          - **NEVER** output a raw string representation of a list (e.g., `[['a', 'b'], ['c', 'd']]`).
        - **Helpfulness**: Synthesize the information found into a clear answer.
        """

        messages = [
            {"role": "system", "content": system_instructions.format(tenant_id=tenant_id)}
        ]
        
        # Add history
        # Simplify history to last 4 turns to save tokens
        for m in history[-4:]:
             messages.append({"role": m["role"], "content": m["content"]})
        
        messages.append({"role": "user", "content": question})

        # --- Loop (Max 5 turns for complex DB exploration) ---
        import json
        from sqlalchemy import text
        
        for _ in range(5):
            try:
                response = self.llm.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.1
                )
                
                msg = response.choices[0].message
                
                if not msg.tool_calls:
                     # Direct answer, we are done
                     return msg.content
                
                # Append assistant message with tool calls
                messages.append(msg.model_dump())
                
                # Execute Tools
                for tool_call in msg.tool_calls:
                    fn_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    result_content = ""
                    

                    
                    try:
                        if fn_name == "search_documents":
                            # Use existing logic logic
                            res = self._search_documents_internal(tenant_id, args["query"])
                            result_content = res if res else "No relevant documents found."
                        
                        elif fn_name == "list_tables":
                            # Query information_schema
                            query = text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                            result = await db.execute(query)
                            tables = [row[0] for row in result.fetchall()]
                            result_content = f"Tables: {', '.join(tables)}"

                        elif fn_name == "describe_table":
                            table_name = args["table_name"]
                            # Sanitize simplistic check
                            if not table_name.replace("_","").isalnum():
                                result_content = "Invalid table name."
                            else:
                                query = text(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table_name}'")
                                result = await db.execute(query)
                                columns = [f"{row[0]} ({row[1]})" for row in result.fetchall()]
                                result_content = f"Schema for {table_name}:\n" + "\n".join(columns)

                        elif fn_name == "sql_query":
                            sql = args["query"].strip()
                            # Basic safety check
                            if not sql.lower().startswith("select"):
                                result_content = "Error: Only SELECT queries are allowed."
                            else:
                                try:
                                    result = await db.execute(text(sql))
                                    # Limit results to avoid context overflow
                                    rows = result.fetchall()
                                    keys = list(result.keys()) if result.keys() else []
                                    
                                    if len(rows) > 20:
                                        result_content = f"Showing first 20 of {len(rows)} rows:\n"
                                        rows = rows[:20]
                                    else:
                                        result_content = ""
                                    
                                    # Format rows nicely with keys
                                    result_content += f"Columns: {keys}\n"
                                    result_content += str([list(row) for row in rows])
                                except Exception as e:
                                    result_content = f"SQL Error: {str(e)}"

                    except Exception as e:
                        result_content = f"Tool execution error: {str(e)}"

                    # Append Tool Output
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_content
                    })
                    
            except Exception as e:
                return "I encountered an internal error while processing your request."

        return "I'm sorry, I couldn't resolve your request after multiple attempts."

    def _search_documents_internal(self, tenant_id: str, query: str) -> str:
        """Helper to run vector search and return context string"""
        vector = self._embed(query)
        if not vector: return ""
        try:
            search_result = self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=vector,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(tenant_id)))]
                ),
                limit=5
            )
            context_parts = []
            for hit in search_result:
                text = hit.payload['text']
                source = hit.payload.get('filename', 'doc')
                context_parts.append(f"[Source: {source}]\n{text}")
            return "\n\n".join(context_parts)
        except Exception:
            return ""

rag_service = RAGService()
