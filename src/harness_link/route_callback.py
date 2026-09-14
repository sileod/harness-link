from litellm.integrations.custom_logger import CustomLogger


ROUTE_PREFIX = "HARNESS_LINK_ROUTE "


class HarnessLinkRouteLogger(CustomLogger):
    async def async_post_call_success_deployment_hook(self, request_data, response, call_type):
        params = request_data.get("litellm_params") or {}
        model = str(request_data.get("model") or "unknown")
        api_base = str(params.get("api_base") or "")
        print(f"{ROUTE_PREFIX}{model}\t{api_base}", flush=True)


proxy_handler_instance = HarnessLinkRouteLogger()
