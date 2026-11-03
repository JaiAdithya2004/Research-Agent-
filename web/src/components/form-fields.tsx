import { Input, Select } from "@/components/ui/input";

export function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="text-sm font-medium text-destructive">
      <span className="sr-only">Error: </span>
      {message}
    </p>
  );
}

export function TextField({
  id,
  label,
  hint,
  error,
  value,
  onChange,
  required,
  placeholder,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  placeholder?: string;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
        {required ? <span className="text-muted-foreground"> (required)</span> : null}
      </label>
      {hint ? (
        <p id={hintId} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        aria-required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      <FieldError id={errorId} message={error} />
    </div>
  );
}

export function SelectField({
  id,
  label,
  value,
  options,
  onChange,
  hint,
}: {
  id: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  hint?: string;
}) {
  const hintId = `${id}-hint`;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      <Select id={id} value={value} aria-describedby={hint ? hintId : undefined} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </div>
  );
}

export function RadioGroupField<T extends string>({
  name,
  legend,
  value,
  options,
  onChange,
}: {
  name: string;
  legend: string;
  value: T;
  options: { value: T; label: string; description: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      {options.map((o) => {
        const id = `${name}-${o.value}`;
        return (
          <label key={o.value} htmlFor={id} className="flex cursor-pointer items-start gap-3 rounded-md border p-3 has-[:checked]:border-ring has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring">
            <input
              id={id}
              type="radio"
              name={name}
              value={o.value}
              checked={value === o.value}
              onChange={() => onChange(o.value)}
              className="mt-1 size-4 accent-[var(--primary)]"
            />
            <span className="flex flex-col">
              <span className="text-sm font-medium">{o.label}</span>
              <span className="text-sm text-muted-foreground">{o.description}</span>
            </span>
          </label>
        );
      })}
    </fieldset>
  );
}
