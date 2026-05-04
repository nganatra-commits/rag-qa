import { ChatWidget } from "@/components/chat-widget";

export default function Page() {
  return (
    <main className="min-h-dvh">
      <div className="max-w-3xl mx-auto px-6 py-16">
        <h1 className="text-3xl font-semibold tracking-tight mb-3">
          NWA Quality Analyst — Knowledge Assistant
        </h1>
        <p className="text-[var(--muted-foreground)] mb-8 max-w-prose">
          Ask anything about installation, the tutorial workflow, or any dialog
          in the User's Manual. Answers cite exact pages and can include the
          actual screenshot inline.
        </p>

        <div className="rounded-xl border border-[var(--border)] bg-[var(--muted)] p-5 max-w-prose">
          <h2 className="font-medium mb-2 text-sm">Try asking:</h2>
          <ul className="text-sm space-y-1.5 text-[var(--muted-foreground)]">
            <li>• "Walk me through running the NWA QA8 installer step by step."</li>
            <li>• "Explain Creating and Editing Data Sets."</li>
            <li>• "What does the Capability Analysis dialog do?"</li>
            <li>• "Walk me through Tutorial Exercise 1."</li>
          </ul>
        </div>

        <p className="text-xs text-[var(--muted-foreground)] mt-8">
          Click the chat bubble in the bottom-right corner to start. Use the
          toggle in the chat header to switch between text-only and image
          answers.
        </p>
      </div>

      <ChatWidget />
    </main>
  );
}
