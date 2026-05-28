import { Mastra } from "@mastra/core";
import { registerCopilotKit } from "@ag-ui/mastra";
import { tripPlannerAgent } from "./agents/trip-planner";

export const mastra = new Mastra({
  agents: { tripPlanner: tripPlannerAgent },
  server: {
    cors: {
      origin: ["http://localhost:3000"],
      allowMethods: ["GET", "POST", "OPTIONS"],
      allowHeaders: ["Content-Type"],
    },
    apiRoutes: [
      registerCopilotKit({
        path: "/copilotkit",
        resourceId: "tripPlanner",
        setContext: (_c, runtimeContext) => {
          runtimeContext.set("user-id", "demo-user");
        },
      }),
    ],
  },
});
