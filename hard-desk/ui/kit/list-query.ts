export type ListQueryValue = string | number | boolean | null | readonly ListQueryValue[] | {
  readonly [key: string]: ListQueryValue | undefined;
};

export interface ListScope {
  actor: string;
  role: string;
  territory: string;
  revision: number;
  access_model?: 1 | 2;
}

export interface ListSort {
  key: string;
  direction: "asc" | "desc";
}

export interface ListQuery {
  scope: ListScope;
  search?: string;
  filters?: Readonly<Record<string, ListQueryValue>>;
  sort?: readonly ListSort[];
  cursor?: string | null;
  limit?: number;
}

function canonical(value: unknown, ancestors: Set<object>, arrayItem = false): string | undefined {
  if (value === undefined) {
    if (arrayItem) throw new TypeError("Undefined list-query array item");
    return undefined;
  }
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("Non-finite list-query number");
    return JSON.stringify(value);
  }
  if (typeof value !== "object") throw new TypeError("Unsupported list-query value");
  if (ancestors.has(value)) throw new TypeError("Circular list-query value");
  const prototype = Object.getPrototypeOf(value);
  const plain = prototype === null || (Object.getPrototypeOf(prototype) === null &&
    Object.hasOwn(prototype, "constructor") && typeof prototype.constructor === "function" && prototype.constructor.name === "Object");
  if (!Array.isArray(value) && !plain) throw new TypeError("Non-plain list-query object");
  ancestors.add(value);
  try {
    if (Array.isArray(value)) return `[${value.map(item => canonical(item, ancestors, true)).join(",")}]`;
    const parts: string[] = [];
    for (const key of Object.keys(value).sort()) {
      const serialized = canonical((value as Record<string, unknown>)[key], ancestors);
      if (serialized !== undefined) parts.push(`${JSON.stringify(key)}:${serialized}`);
    }
    return `{${parts.join(",")}}`;
  } finally {
    ancestors.delete(value);
  }
}

function serialize(value: unknown): string {
  const result = canonical(value, new Set());
  if (result === undefined) throw new TypeError("Undefined list-query value");
  return result;
}

function checked(query: ListQuery): void {
  if (!query || typeof query !== "object" || !query.scope) throw new TypeError("List scope is required");
  const { actor, role, territory, revision } = query.scope;
  if (typeof actor !== "string" || typeof role !== "string" || typeof territory !== "string") {
    throw new TypeError("Invalid list scope");
  }
  if (!Number.isInteger(revision) || revision < 0) throw new TypeError("Invalid list-scope revision");
  if (query.limit !== undefined && (!Number.isInteger(query.limit) || query.limit <= 0)) {
    throw new TypeError("Invalid list limit");
  }
}

export function serializeListQuery(query: ListQuery): string {
  checked(query);
  return serialize(query);
}

export function listQuerySearchParams(query: ListQuery): URLSearchParams {
  checked(query);
  const params = new URLSearchParams();
  params.set("actor", query.scope.actor);
  params.set("role", query.scope.role);
  params.set("territory", query.scope.territory);
  params.set("revision", String(query.scope.revision));
  if (query.scope.access_model !== undefined) params.set("access_model", String(query.scope.access_model));
  if (query.search !== undefined) params.set("search", query.search);
  if (query.filters !== undefined) params.set("filters", serialize(query.filters));
  if (query.sort !== undefined) params.set("sort", serialize(query.sort));
  if (query.cursor !== undefined && query.cursor !== null) params.set("cursor", query.cursor);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  return params;
}
