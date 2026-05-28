import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { NextRequest } from "next/server";

// Proxies the browser to BOTH Mastra AG-UI endpoints. CopilotKit dispatches
// to the right one based on the `agent` name the frontend specifies.
const runtime = new CopilotRuntime({
  remoteEndpoints: [
    { url: "http://localhost:4111/copilotkit/trip-planner" },
    { url: "http://localhost:4111/copilotkit/settlement-break" },
  ],
});

export const POST = async (req: NextRequest) => {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
};
