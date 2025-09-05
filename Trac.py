Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "C:\Users\ypathak1\podcast_generator\.venv\Scripts\langgraph.exe\__main__.py", line 6, in <module>
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\click\core.py", line 1442, in __call__
    return self.main(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\click\core.py", line 1363, in main
    rv = self.invoke(ctx)
         ^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\click\core.py", line 1830, in invoke
    return _process_result(sub_ctx.command.invoke(sub_ctx))
                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\click\core.py", line 1226, in invoke
    return ctx.invoke(self.callback, **ctx.params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\click\core.py", line 794, in invoke
    return callback(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\langgraph_cli\analytics.py", line 96, in decorator
    return func(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\langgraph_cli\cli.py", line 733, in dev
    config_json = langgraph_cli.config.validate_config_file(pathlib.Path(config))
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\langgraph_cli\config.py", line 737, in validate_config_file
    validated = validate_config(config)
                ^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\langgraph_cli\config.py", line 592, in validate_config
    some_node = any(_is_node_graph(spec) for spec in graphs.values())
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\langgraph_cli\config.py", line 592, in <genexpr>
    some_node = any(_is_node_graph(spec) for spec in graphs.values())
                    ^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\ypathak1\podcast_generator\.venv\Lib\site-packages\langgraph_cli\config.py", line 574, in _is_node_graph
    file_path = spec.split(":")[0]
                ^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'split'
