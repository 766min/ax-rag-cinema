"""LangChain tools for the movie question and answer flow."""

from tools.translation import translate_movie_answer, translate_movie_query

ALL_TOOLS = [translate_movie_query, translate_movie_answer]
