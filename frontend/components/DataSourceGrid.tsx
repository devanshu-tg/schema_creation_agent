'use client';

import clsx from 'clsx';
import {
  Database,
  FileSpreadsheet,
  HardDrive,
  type LucideIcon,
  Radio,
  Snowflake,
} from 'lucide-react';
import { useCallback } from 'react';
import { useDropzone } from 'react-dropzone';

interface DataSourceDef {
  id: string;
  label: string;
  description: string;
  icon: LucideIcon;
  enabled: boolean;
}

const SOURCES: DataSourceDef[] = [
  {
    id: 'snowflake',
    label: 'Connect Snowflake',
    description: 'Warehouse table or view',
    icon: Snowflake,
    enabled: false,
  },
  {
    id: 'database',
    label: 'Connect Database',
    description: 'Postgres, MySQL, SQL Server',
    icon: Database,
    enabled: false,
  },
  {
    id: 'api',
    label: 'Connect API',
    description: 'REST, Kafka, Pulsar, …',
    icon: Radio,
    enabled: false,
  },
  {
    id: 'cloud',
    label: 'Connect Cloud Storage',
    description: 'S3, GCS, Azure Blob',
    icon: HardDrive,
    enabled: false,
  },
  {
    id: 'upload',
    label: 'Upload CSV',
    description: 'Local CSV, Parquet, or Excel',
    icon: FileSpreadsheet,
    enabled: true,
  },
];

interface Props {
  selected: string | null;
  onSelect: (id: string) => void;
  onFilesPicked: (files: File[]) => void;
  uploadedName: string | null;
}

export default function DataSourceGrid({
  selected,
  onSelect,
  onFilesPicked,
  uploadedName,
}: Props) {
  const onDrop = useCallback(
    (accepted: File[]) => {
      if (accepted.length) {
        onSelect('upload');
        onFilesPicked(accepted);
      }
    },
    [onFilesPicked, onSelect],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    multiple: true,
    accept: { 'text/csv': ['.csv'], 'application/octet-stream': ['.csv'] },
    noClick: true,
    noKeyboard: true,
  });

  return (
    <div {...getRootProps()} className="space-y-2">
      <input {...getInputProps()} />
      {SOURCES.map((s) => {
        const Icon = s.icon;
        const isUpload = s.id === 'upload';
        return (
          <button
            key={s.id}
            type="button"
            disabled={!s.enabled}
            onClick={() => {
              if (!s.enabled) return;
              if (isUpload) open();
              onSelect(s.id);
            }}
            title={!s.enabled ? `${s.label} — coming soon` : s.description}
            className={clsx(
              'flex w-full items-center gap-2.5 rounded-lg border bg-tgl-card px-3 py-2.5 text-left text-[12.5px] font-medium text-tgl-ink transition-colors',
              s.enabled
                ? 'border-tgl-border hover:border-tg-orange hover:bg-tgl-bubble'
                : 'cursor-not-allowed border-tgl-border opacity-60',
              isUpload && isDragActive && 'border-tg-orange bg-tgl-chip',
            )}
          >
            <Icon size={15} className="text-tgl-mute" />
            <span className="flex-1">{s.label}</span>
            {!s.enabled && (
              <span className="rounded-full bg-tgl-bubble px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-tgl-mute">
                soon
              </span>
            )}
          </button>
        );
      })}

      {uploadedName && (
        <div className="rounded-lg bg-tgl-activeBg px-3 py-2 text-[12px] text-tgl-activeInk">
          ✓ <span className="font-medium">{uploadedName}</span> uploaded
        </div>
      )}
    </div>
  );
}
