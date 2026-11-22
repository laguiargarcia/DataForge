"""Fixtures shared by the pipeline tests.

The engine fixtures (dataforge_home, db_url, global_engine) and the Hypothesis profiles come from
DataForge's pytest plugin, the same one the engine's tests load: the tests that run this workspace's
job.yaml through the real engine (test_job_wiring) use them. Every database and DATAFORGE_HOME is a
temp dir.
"""
pytest_plugins = ["dataforge.testing"]
