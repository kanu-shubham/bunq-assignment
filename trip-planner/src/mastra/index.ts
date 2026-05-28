import { Mastra } from "@mastra/core";
import { registerCopilotKit } from "@ag-ui/mastra";
import { tripPlannerAgent } from "./agents/trip-planner";
import { settlementBreakAgent } from "./agents/settlement-break";

export const mastra = new Mastra({
  agents: {
    tripPlanner: tripPlannerAgent,
    settlementBreak: settlementBreakAgent,
  },
  server: {
    cors: {
      origin: ["http://localhost:3000"],
      allowMethods: ["GET", "POST", "OPTIONS"],
      allowHeaders: ["Content-Type"],
    },
    apiRoutes: [
      registerCopilotKit({
        path: "/copilotkit/trip-planner",
        resourceId: "tripPlanner",
        setContext: (_c, runtimeContext) => {
          runtimeContext.set("user-id", "demo-user");
        },
      }),
      registerCopilotKit({
        path: "/copilotkit/settlement-break",
        resourceId: "settlementBreak",
        setContext: (_c, runtimeContext) => {
          runtimeContext.set("user-id", "demo-user");
        },
      }),
    ],
  },
});
