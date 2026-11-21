idf_component_register(SRCS "main.c"
                       "httpd.c"
                       "cfg.c"
                       "relay.c"
                       "sched.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_http_server nvs_flash json pthread)
