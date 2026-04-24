# Shared application state and constants

ONET_CACHE = {}

MARKET_INSIGHTS_TTL_HOURS = 24  # Cache market insights for 24 hours
RESOURCE_CACHE_TTL_DAYS = 30    # Cache YouTube/Tavily resources for 30 days
EXA_CACHE_TTL_DAYS = 15         # Cache Exa market data for 15 days (job market changes slowly)
ROLE_CONTEXT_CACHE_TTL_DAYS = 15  # Cache O*NET + Exa role context for 15 days

# RAG pipeline limits
RAG_MAX_SOURCES = 40
RAG_CONTEXT_CHAR_LIMIT = 12000

# LLM defaults
LLM_MODEL = "llama-3.3-70b-versatile"
LLM_TEMPERATURE = 0.2

# Phase query generator (fast model for search query generation)
PHASE_QUERY_MODEL = "llama-3.1-8b-instant"
PHASE_RESOURCES_PER_TYPE = {"articles": 3, "books": 2, "videos": 3}

# Shared learning path cache
LEARNING_PATH_PROMPT_VERSION = 1

# Test configuration
TEST_PASSING_SCORE = 70  # percent
TEST_QUESTIONS_COUNT = 15

# Video anti-cheat
VIDEO_HEARTBEAT_INTERVAL_SECONDS = 5
VIDEO_MAX_SEEK_TOLERANCE_SECONDS = 15

