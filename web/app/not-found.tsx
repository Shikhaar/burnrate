import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
      <p className="font-mono text-sm text-accent">404</p>
      <h1 className="text-xl font-semibold text-fg">Page not found</h1>
      <Link href="/" className="text-sm text-muted hover:text-fg">
        Back to overview
      </Link>
    </div>
  );
}
