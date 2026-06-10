'use client';

import clsx from 'clsx';
import {
  CloudUpload,
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
    id: 'upload',
    label: 'Upload file',
    description: 'Local CSV, Parquet, or Excel',
    icon: CloudUpload,
    enabled: true,
  },
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
    id: 'cloud',
    label: 'Cloud Storage',
    description: 'S3, GCS, Azure Blob',
    icon: HardDrive,
    enabled: false,
  },
  {
    id: 'api',
    label: 'API / Stream',
    description: 'REST, Kafka, Pulsar, …',
    icon: Radio,
    enabled: false,
  },
  {
    id: 'sample',
    label: 'Sample Dataset',
    description: 'Explore with built-in data',
    icon: FileSpreadsheet,
    enabled: false,
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
    <div {...getRootProps()} className="space-y-3">
      <input {...getInputProps()} />
      <div className="grid grid-cols-3 gap-3">
        {SOURCES.map((s) => {
          const Icon = s.icon;
          const isSelected = selected === s.id;
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
              className={clsx(
                'flex flex-col items-start gap-2 rounded-xl border bg-tg-card p-3 text-left transition-all',
                isSelected
                  ? 'border-tg-purple shadow-card-hover ring-1 ring-tg-purple-100'
                  : 'border-tg-line shadow-card hover:border-tg-purple hover:bg-tg-hover',
                !s.enabled &&
                  'cursor-not-allowed opacity-50 hover:border-tg-line hover:bg-tg-card',
                isUpload && isDragActive && 'border-tg-purple bg-tg-purple-100',
              )}
            >
              <div
                className={clsx(
                  'flex h-8 w-8 items-center justify-center rounded-lg',
                  isSelected ? 'bg-tg-purple-100 text-tg-purple-500' : 'bg-tg-hover text-tg-mute',
                )}
              >
                <Icon size={15} />
              </div>
              <div className="flex w-full items-center justify-between">
                <span className="text-[12.5px] font-semibold text-tg-ink">{s.label}</span>
                {!s.enabled && (
                  <span className="rounded-full bg-tg-hover px-1.5 py-0.5 text-[9px] text-tg-mute">
                    soon
                  </span>
                )}
              </div>
              <p className="text-[11px] leading-snug text-tg-mute">{s.description}</p>
            </button>
          );
        })}
      </div>

      {uploadedName && (
        <div className="rounded-lg border border-tg-purple-100 bg-tg-purple-100 px-3 py-2 text-[12px] text-tg-purple-700">
          ✓ <span className="font-medium">{uploadedName}</span> uploaded
        </div>
      )}

      <div className="text-[11px] text-tg-mute">
        For data sources we currently support file loading only.
      </div>
    </div>
  );
}
