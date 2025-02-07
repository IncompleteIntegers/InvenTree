import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '../../enums/ApiEndpoints';
import { ModelType } from '../../enums/ModelType';
import { getDetailUrl } from '../../functions/urls';
import { useTable } from '../../hooks/UseTable';
import { apiUrl } from '../../states/ApiState';
import { useUserState } from '../../states/UserState';
import { TableColumn } from '../Column';
import { PartColumn, StatusColumn } from '../ColumnRenderers';
import { InvenTreeTable } from '../InvenTreeTable';

export default function InstalledItemsTable({
  parentId
}: {
  parentId?: number | string;
}) {
  const table = useTable('stock_item_install');
  const user = useUserState();
  const navigate = useNavigate();

  const tableColumns: TableColumn[] = useMemo(() => {
    return [
      {
        accessor: 'part',
        switchable: false,
        render: (record: any) => PartColumn(record?.part_detail)
      },
      {
        accessor: 'quantity',
        switchable: false,
        render: (record: any) => {
          let text = record.quantity;

          if (record.serial && record.quantity == 1) {
            text = `# ${record.serial}`;
          }

          return text;
        }
      },
      {
        accessor: 'batch',
        switchable: false
      },
      StatusColumn(ModelType.stockitem)
    ];
  }, []);

  const tableActions = useMemo(() => {
    return [];
  }, [user]);

  return (
    <>
      <InvenTreeTable
        url={apiUrl(ApiEndpoints.stock_item_list)}
        tableState={table}
        columns={tableColumns}
        props={{
          tableActions: tableActions,
          onRowClick: (record: any) => {
            if (record.pk) {
              navigate(getDetailUrl(ModelType.stockitem, record.pk));
            }
          },
          params: {
            belongs_to: parentId,
            part_detail: true
          }
        }}
      />
    </>
  );
}
