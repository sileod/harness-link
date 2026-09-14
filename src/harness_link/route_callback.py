from litellm.integrations.custom_logger import CustomLogger


ROUTE_PREFIX = "HARNESS_LINK_ROUTE "


class HarnessLinkRouteLogger(CustomLogger):
    async def async_post_call_success_deployment_hook(self, request_data, response, call_type):
        route = str(request_data.get("model") or "unknown")
        print(ROUTE_PREFIX + route, flush=True)


proxy_handler_instance = HarnessLinkRouteLogger()
